"""HTTP integration tests DB-backed replay protection (Этап 1 ТЗ).

Единица проверки — реальный URL/view ContactPage (production-код SpamProtectedForm),
а не повторение модельной логики. Unit-тесты модели остались в blog/tests_replay.py.

Сценарии ТЗ (3.4):
  1. GET → токен из HTML → POST → успех + строка UsedFormToken.
  2. Второй POST с тем же токеном → контролируемое отклонение.
  3. Новый токен → успех (и БД работает после пойманного IntegrityError).
  4. Две сессии получают разные токены и не блокируют друг друга.
  5. Конкурентные POST с одним токеном → ровно один успешный (view-уровень).
  6. Истёкший токен отвергается даже после cleanup_expired_tokens.
  7. Очистка security/default/pages кэшей не меняет replay-результат.
  8. После пойманного IntegrityError БД в той же транзакции работает.
  9. cleanup вызывается через call_command, не копированием SQL.
  + CSRF-поток с enforce_csrf_checks, honeypot, слишком быстрый submit,
    сбой БД = fail-closed (не молчаливое разрешение).
"""
import hashlib
from datetime import timedelta

import io
import re
import secrets
import threading
import time as _time
import uuid
from unittest import mock

from django.core import signing
from django.core.cache import caches
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from wagtail.models import Page, Site

from blog.models import ContactPage, FormField, UsedFormToken

SALT = "blog.contact_form"
TOKEN_RE = re.compile(r'<input[^>]*name="form_token"[^>]*value="([^"]*)"')
CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')


def _token_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def _signed_token(nonce: str, issued_at: float = None) -> str:
    """Подписанный токен тем же salt, что production-код (для expiry/конкурентности)."""
    if issued_at is None:
        issued_at = _time.time() - 5  # возраст 5 c: проходит проверку age >= 4
    return signing.dumps({"issued_at": issued_at, "nonce": nonce},
                         salt=SALT, compress=True)


class ContactFormTestMixin:
    """Создание ContactPage с полями и вспомогательные POST-хелперы."""

    @classmethod
    def _make_contact_page(cls):
        root = Page.get_first_root_node()
        if root is None:
            # После flush (TransactionTestCase) дерево/локаль отсутствуют —
            # восстанавливаем минимум (treebeard add_root требует Locale).
            from django.conf import settings as dj_settings
            from wagtail.models import Locale
            if not Locale.objects.exists():
                Locale.objects.create(
                    language_code=dj_settings.LANGUAGE_CODE)
            root = Page.add_root(instance=Page(title="Root", slug="root"))
        cls.contact = root.add_child(instance=ContactPage(
            title="Контакты", slug=f"contact-{uuid.uuid4().hex[:8]}",
            intro="Пишите нам", thank_you_text="Спасибо!",
            from_address="from@test.example", to_address="to@test.example",
            subject="[Test] Сообщение",
        ))
        for i, (name, ftype, req) in enumerate(
                [("name", "singleline", True),
                 ("email", "email", True),
                 ("message", "multiline", True)], start=1):
            FormField.objects.create(page=cls.contact, sort_order=i,
                                     clean_name=name, label=name, field_type=ftype,
                                     required=req)
        site = Site.objects.filter(is_default_site=True).first()
        if site is None:
            cls.site = Site.objects.create(hostname="testserver",
                                           root_page=cls.contact,
                                           is_default_site=True)
        else:
            cls.site = site
            site.root_page = cls.contact
            site.save()
        cls.url = cls.contact.url

    def get_form(self, client=None):
        c = client or self.client
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 200, "GET контактной формы должен быть 200")
        m = TOKEN_RE.search(resp.content.decode())
        self.assertIsNotNone(m, "в HTML должен быть hidden input form_token")
        return resp, m.group(1)

    def post_form(self, token, *, client=None, shift=0, **overrides):
        """POST с реальными данными формы. shift — сдвиг time.time вперёд (age>=4)."""
        data = {
            "name": "Иван Тестов",
            "email": "ivan@test.example",
            "message": "Тестовое сообщение",
            "company_website": "",
            "form_token": token,
        }
        data.update(overrides)
        c = client or self.client
        if shift:
            ctx = mock.patch("time.time", return_value=_time.time() + shift)
        else:
            import contextlib
            ctx = contextlib.nullcontext()
        with ctx:
            return c.post(self.url, data)


class ReplayHTTPFlowTests(ContactFormTestMixin, TestCase):
    """Однопоточные сценарии через реальный view (TestCase — транзакция на класс)."""

    @classmethod
    def setUpTestData(cls):
        cls._make_contact_page()

    def setUp(self):
        caches["security"].clear()

    # --- 1. Полный поток: GET → токен → POST → UsedFormToken ---
    def test_full_flow_first_post_accepted(self):
        resp, token = self.get_form()
        self.assertNotEqual(token, "", "токен не должен быть пустым")
        r = self.post_form(token, shift=10)
        self.assertEqual(r.status_code, 200)
        self.assertIn("landing", r.template_name, "успех = landing-шаблон")
        nonce = signing.loads(token, salt=SALT)
        self.assertTrue(UsedFormToken.objects.filter(
            token_hash=_token_hash(nonce["nonce"])).exists(),
            "использованный токен должен быть зарегистрирован в БД")

    # --- 2. Повторный POST с тем же токеном ---
    def test_second_post_same_token_rejected(self):
        _, token = self.get_form()
        r1 = self.post_form(token, shift=10)
        self.assertIn("landing", r1.template_name)
        nonce = signing.loads(token, salt=SALT)["nonce"]
        r2 = self.post_form(token, shift=20)
        self.assertEqual(r2.status_code, 200)
        self.assertIn("уже была отправлена", r2.content.decode(),
                      "повтор должен получить контролируемое сообщение о replay")
        self.assertEqual(UsedFormToken.objects.filter(token_hash=_token_hash(nonce)).count(), 1)

    # --- 3+8. Новый токен принимается; БД работает после IntegrityError ---
    def test_new_token_accepted_after_replay_and_db_usable(self):
        _, token = self.get_form()
        self.assertIn("landing", self.post_form(token, shift=10).template_name)
        # replay → IntegrityError пойман внутри view-потока
        r2 = self.post_form(token, shift=20)
        self.assertIn("уже была отправлена", r2.content.decode())
        # БД в той же тестовой транзакции продолжает работать
        self.assertTrue(UsedFormToken.objects.count() >= 1)
        self.assertTrue(Page.objects.exists())
        # новый токен → успешный POST
        _, token2 = self.get_form()
        r3 = self.post_form(token2, shift=10)
        self.assertIn("landing", r3.template_name)

    # --- 4. Две сессии: разные токены, не блокируют друг друга ---
    def test_two_sessions_distinct_tokens_both_work(self):
        c1, c2 = Client(), Client()
        _, t1 = self.get_form(c1)
        _, t2 = self.get_form(c2)
        self.assertNotEqual(t1, t2, "каждая сессия получает индивидуальный токен")
        r1 = self.post_form(t1, client=c1, shift=10)
        r2 = self.post_form(t2, client=c2, shift=10)
        self.assertIn("landing", r1.template_name)
        self.assertIn("landing", r2.template_name)

    # --- 7. Очистка кэшей не меняет replay-результат ---
    def test_cache_clear_does_not_change_replay_result(self):
        _, token = self.get_form()
        self.assertIn("landing", self.post_form(token, shift=10).template_name)
        for alias in ("default", "security", "pages"):
            caches[alias].clear()
        r = self.post_form(token, shift=20)
        self.assertIn("уже была отправлена", r.content.decode())

    # --- 6. Истёкший токен отвергается даже после cleanup ---
    def test_expired_token_rejected_even_after_cleanup(self):
        nonce = secrets.token_urlsafe(18)
        max_age = int(getattr(__import__("django.conf", fromlist=["settings"]).settings,
                              "CONTACT_FORM_TOKEN_MAX_AGE", 24 * 60 * 60))
        token = _signed_token(nonce, issued_at=_time.time() - max_age - 10)
        # cleanup уже прошёл: строк нет и не будет
        call_command("cleanup_expired_tokens", stdout=io.StringIO())
        r = self.post_form(token)
        self.assertIn("устарела", r.content.decode())
        self.assertFalse(UsedFormToken.objects.filter(token_hash=_token_hash(nonce)).exists(),
                         "просроченный токен не должен регистрироваться")

    # --- 9. cleanup через call_command ---
    def test_cleanup_command_via_call_command(self):
        from django.conf import settings
        max_age = int(getattr(settings, "CONTACT_FORM_TOKEN_MAX_AGE", 24 * 60 * 60))
        old = UsedFormToken.objects.create(token_hash="cmd-old")
        UsedFormToken.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(seconds=max_age + 1))
        fresh = UsedFormToken.objects.create(token_hash="cmd-fresh")
        out = io.StringIO()
        call_command("cleanup_expired_tokens", stdout=out)
        self.assertFalse(UsedFormToken.objects.filter(pk=old.pk).exists())
        self.assertTrue(UsedFormToken.objects.filter(pk=fresh.pk).exists())
        self.assertIn("1", out.getvalue())

    # --- дополнительные защитные свойства ---
    def test_too_fast_submission_rejected(self):
        _, token = self.get_form()
        r = self.post_form(token)  # без сдвига времени: age < 4
        self.assertIn("слишком быстро", r.content.decode())
        nonce = signing.loads(token, salt=SALT)["nonce"]
        self.assertEqual(UsedFormToken.objects.filter(token_hash=_token_hash(nonce)).count(), 0)

    def test_honeypot_rejected_without_consuming_token(self):
        _, token = self.get_form()
        r = self.post_form(token, shift=10, company_website="http://spam.example")
        self.assertIn("Спам-активность", r.content.decode())
        nonce = signing.loads(token, salt=SALT)["nonce"]
        self.assertEqual(UsedFormToken.objects.filter(token_hash=_token_hash(nonce)).count(), 0,
                         "токен не должен списываться при ошибке валидации")

    def test_token_preserved_on_field_validation_error(self):
        _, token = self.get_form()
        r = self.post_form(token, shift=10, message="")  # пустое обязательное поле
        self.assertContains(r, "required", status_code=200)
        nonce = signing.loads(token, salt=SALT)["nonce"]
        self.assertEqual(UsedFormToken.objects.filter(token_hash=_token_hash(nonce)).count(), 0)
        # токен остаётся пригодным для полноценного submit
        r2 = self.post_form(token, shift=15)
        self.assertIn("landing", r2.template_name)

    def test_missing_token_rejected(self):
        r = self.post_form("", shift=10)
        self.assertIn("Не удалось проверить форму", r.content.decode())

    def test_tampered_signature_rejected(self):
        r = self.post_form("tampered-token-value", shift=10)
        self.assertIn("неверную подпись", r.content.decode())

    def test_pgcode_on_real_unique_violation(self):
        """Фактический драйвер (psycopg2) выставляет pgcode 23505 на реальном
        нарушении unique — классификация replay по pgcode обоснована."""
        from django.db import IntegrityError as DjangoIntegrityError, transaction
        from blog.models import UsedFormToken as M
        M.objects.create(token_hash="pgcode-proof")
        with self.assertRaises(DjangoIntegrityError) as ctx:
            with transaction.atomic():
                M.objects.create(token_hash="pgcode-proof")
        self.assertEqual(getattr(ctx.exception.__cause__, "pgcode", None), "23505",
                         "драйвер должен передавать sqlstate в __cause__.pgcode")

    def test_unique_violation_vs_other_integrity_error(self):
        """unique violation (23505) = replay; иная IntegrityError = fail-closed."""
        from django.db import IntegrityError as DjangoIntegrityError

        class _Cause(Exception):
            def __init__(self, code):
                super().__init__("db")
                self.pgcode = code

        # __cause__ появляется только через "raise X from Y" — как в
        # реальном обёрточном слое django.db.backends.
        def _integrity(code):
            def _fn(*a, **kw):
                try:
                    raise _Cause(code)
                except _Cause as c:
                    raise DjangoIntegrityError("db") from c
            return _fn

        _, token = self.get_form()
        # 1) unique violation -> контролируемое сообщение о повторе
        with mock.patch("blog.models.UsedFormToken.objects.create",
                        side_effect=_integrity("23505")):
            r1 = self.post_form(token, shift=10)
        self.assertIn("уже была отправлена", r1.content.decode())
        # 2) иная ошибка целостности (FK, 23503) -> fail-closed, не replay
        _, token2 = self.get_form()
        with mock.patch("blog.models.UsedFormToken.objects.create",
                        side_effect=_integrity("23503")):
            r2 = self.post_form(token2, shift=10)
        self.assertIn("временно недоступна", r2.content.decode(),
                      "не-unique IntegrityError не должен считаться replay")
        self.assertNotIn("уже была отправлена", r2.content.decode())
        # 3) pgcode отсутствует/неизвестен (драйвер без sqlstate) ->
        #    fail-closed: НЕ считаем это replay (политика координатора)
        _, token3 = self.get_form()
        with mock.patch("blog.models.UsedFormToken.objects.create",
                        side_effect=_integrity(None)):
            r3 = self.post_form(token3, shift=10)
        self.assertIn("временно недоступна", r3.content.decode(),
                      "неизвестный код целостности не должен считаться replay")
        self.assertNotIn("уже была отправлена", r3.content.decode())

    def test_db_error_fails_closed(self):
        _, token = self.get_form()
        with mock.patch("blog.models.UsedFormToken.objects.create",
                        side_effect=RuntimeError("db down")):
            r = self.post_form(token, shift=10)
        self.assertIn("временно недоступна", r.content.decode(),
                      "сбой БД должен fail-closed, а не разрешать отправку")
        self.assertEqual(len(r.context["form"].errors), 1)

    # --- CSRF: настоящий поток с enforce_csrf_checks ---
    def test_csrf_enforced_real_flow(self):
        c = Client(enforce_csrf_checks=True)
        resp, token = self.get_form(c)
        m = CSRF_RE.search(resp.content.decode())
        self.assertIsNotNone(m, "шаблон должен содержать csrfmiddlewaretoken")
        csrf = m.group(1)
        # POST без CSRF-токена → 403
        r403 = c.post(self.url, {"name": "x", "email": "x@x.ru", "message": "x",
                                 "form_token": token})
        self.assertEqual(r403.status_code, 403)
        # POST с CSRF-токеном → успех
        r = self.post_form(token, client=c, shift=10, csrfmiddlewaretoken=csrf)
        self.assertIn("landing", r.template_name)


class FlushCascadeMixin:
    """Общая очистка для тестов с реальными коммитами потоков.

    Легаси-таблица blog_comment (см. миграцию 0032 SeparateDatabaseAndState)
    не входит в sqlflush; стандартный flush TransactionTestCase без CASCADE
    падает на postgres. Разрешаем CASCADE, чтобы каскадно захватить и
    таблицу-сироту. Коммиты потоков переживают откат TestCase — поэтому
    такие тесты обязаны быть TransactionTestCase.
    """

    def _fixture_teardown(self):
        from django.core.management import call_command
        call_command("flush", verbosity=0, interactive=False,
                     database=self._databases_names()[0],
                     reset_sequences=False, allow_cascade=True)


class ConcurrentInsertRaceTests(FlushCascadeMixin, TransactionTestCase):
    """Конкурентность на уровне модели (точка сериализации view) — 2 потока, реальная БД.

    TransactionTestCase (не TestCase): соединения потоков живут вне внешней
    транзакции TestCase, их коммиты не откатываются класс-обёрткой; здесь
    очистка тестовой БД происходит штатным flush после каждого теста.
    """

    def test_concurrent_identical_inserts_one_wins(self):
        h = _token_hash(f"race-{uuid.uuid4().hex}")
        barrier = threading.Barrier(2)
        results = []

        def worker():
            try:
                barrier.wait(timeout=10)
                try:
                    UsedFormToken.objects.create(token_hash=h)
                    results.append("ok")
                except IntegrityError:
                    results.append("replay")
            finally:
                from django.db import connection
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(sorted(results), ["ok", "replay"],
                         "ровно один конкурентный INSERT должен пройти")
        self.assertEqual(UsedFormToken.objects.filter(token_hash=h).count(), 1)
        # flush TransactionTestCase обязан убрать коммиты потоков из тестовой БД
        self.assertEqual(UsedFormToken.objects.all().count(), 1,
                         "после flush не должно остаться посторонних коммитов потоков")


class ConcurrentViewReplayTests(FlushCascadeMixin, ContactFormTestMixin, TransactionTestCase):
    """Конкурентные POST через реальный view (TransactionTestCase: нужны commit'ы потоков)."""

    def setUp(self):
        self._make_contact_page()

    def test_two_concurrent_posts_same_token_one_success(self):
        nonce = secrets.token_urlsafe(18)
        token = _signed_token(nonce)  # возраст 5 c: без сдвига времени в потоках
        barrier = threading.Barrier(2)
        outcomes = {}

        def worker(idx):
            c = Client()
            try:
                barrier.wait(timeout=10)
                r = c.post(self.url, {"name": "Иван", "email": "ivan@test.example",
                                      "message": "msg", "company_website": "",
                                      "form_token": token})
                outcomes[idx] = "ok" if "landing" in r.template_name else "rejected"
            except Exception as e:  # noqa: BLE001 — фиксируем любую ошибку как отказ
                outcomes[idx] = f"error:{type(e).__name__}"
            finally:
                from django.db import connection
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(outcomes.values()), ["ok", "rejected"],
                         f"ровно один конкурентный POST должен пройти, got: {outcomes}")
        self.assertEqual(
            UsedFormToken.objects.filter(token_hash=_token_hash(nonce)).count(), 1,
            "в БД должна остаться ровно одна запись токена")
