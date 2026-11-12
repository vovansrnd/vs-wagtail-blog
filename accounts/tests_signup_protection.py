"""Тесты антибот-защиты регистрации (Д50/Д51, координатор 2026-11-06).

Сценарии (таблица координатора): корректная регистрация работает; заполненный
honeypot / неверный ответ / просроченное или использованное задание /
превышение лимита IP — пользователь не создаётся (429 для лимита); легальная
регистрация после лимита — работает; вход существующего пользователя не
затронут. Почта — locmem (реальных писем нет). Тесты выполняются на
ХОСТ-ПРОЕКТЕ (маршруты /comments и /accounts в urlconf проекта).
"""
import re

from django.core.cache import caches
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase

from django.contrib.auth import get_user_model

from django.core import signing as dj_signing

from .forms import MATH_CACHE, MATH_TTL, consume_math_task

User = get_user_model()

SIGNUP_URL = "/accounts/signup/"

# Д60: семантика allauth-лимитера зависит от версии. В 65.21.1 в cache-ключ
# добавлен str(rate) (core/internal/ratelimit.py) — каждая ставка ведёт СВОЮ
# историю, фактический лимит номинальный: 10 POST/10м. В 65.20.1 обе ставки
# (10/10m, 30/d) писали в ОДНУ историю — POST = 2 хита, фактический лимит
# 5 POST/10м. Прод = 65.21.1 (номинальный), dev-стенд = 65.20.1.
import importlib.metadata  # noqa: E402

_ALLAUTH = tuple(
    int(x) for x in importlib.metadata.version("django-allauth").split(".")[:2]
)
EFFECTIVE_POST_LIMIT = 10 if _ALLAUTH >= (65, 21) else 5


def _security_keys():
    """Все ключи кэша security (файловый бэкенд — каталог читаем)."""
    import glob

    return [p for p in glob.glob("/opt/MyApps/wagtail/wagtail-blogs/cache/security/*")]


def _get_signup_page(client):
    """GET страницы регистрации -> (response, task_id, question, answer)."""
    response = client.get(SIGNUP_URL)
    html = response.content.decode()
    m_id = re.search(r'name="math_token"[^>]*value="([^"]+)"', html)
    m_q = re.search(r'for="id_math_answer"[^>]*>([\s\S]*?)</label>', html)
    question = re.sub(r"<[^>]+>", " ", m_q.group(1)) if m_q else ""
    q = re.search(r"(\d+)\s*([+-])\s*(\d+)", question)
    task_id = m_id.group(1) if m_id else None
    answer = None
    if q and task_id:
        a, op, b = int(q.group(1)), q.group(2), int(q.group(3))
        answer = a + b if op == "+" else a - b
    return response, task_id, question.strip(), answer


class SignupProtectionTests(TestCase):
    """Антибот-защита /accounts/signup/ (Д50/Д51)."""

    @classmethod
    def setUpTestData(cls):
        cls.existing = User.objects.create_user(
            username="olduser", password="old-pass-123", email="old@example.test"
        )

    def setUp(self):
        # Изоляция лимитера, заданий и почты между тестами
        caches["default"].clear()
        caches["security"].clear()
        mail.outbox = []
        self.client = self.client_class(REMOTE_ADDR="203.0.113.50")

    def _get_signup_page(self):
        """GET страницы регистрации -> (response, task_id, question, answer)."""
        response = self.client.get(SIGNUP_URL)
        html = response.content.decode()
        m_id = re.search(r'name="math_token"[^>]*value="([^"]+)"', html)
        m_q = re.search(r'for="id_math_answer"[^>]*>([\s\S]*?)</label>', html)
        question = re.sub(r"<[^>]+>", " ", m_q.group(1)) if m_q else ""
        q = re.search(r"(\d+)\s*([+-])\s*(\d+)", question)
        task_id = m_id.group(1) if m_id else None
        answer = None
        if q and task_id:
            a, op, b = int(q.group(1)), q.group(2), int(q.group(3))
            answer = a + b if op == "+" else a - b
        return response, task_id, question.strip(), answer

    def _post_signup(self, task_id, answer, **extra):
        payload = {
            "email": "newuser@example.test",
            "username": "newuser",
            "password1": "Strong-Pass-9x7",
            "password2": "Strong-Pass-9x7",
            "company": "",
            "math_token": task_id or "",
            "math_answer": str(answer) if answer is not None else "",
            "parent": "",
        }
        payload.update(extra)
        return self.client.post(SIGNUP_URL, payload)

    def test_valid_signup_creates_user(self):
        """Корректный honeypot + правильный ответ -> регистрация работает."""
        _r, task_id, _q, answer = self._get_signup_page()
        response = self._post_signup(task_id, answer)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="newuser").exists())

    def test_filled_honeypot_rejected_no_user_no_mail(self):
        """Заполненный honeypot -> пользователя нет, письма нет."""
        _r, task_id, _q, answer = self._get_signup_page()
        response = self._post_signup(task_id, answer, company="Спам-Оптимизация ООО")
        self.assertEqual(response.status_code, 200)  # форма перерисована с ошибкой
        self.assertFalse(User.objects.filter(username="newuser").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_math_answer_rejected(self):
        """Неверный ответ -> пользователя нет, письма нет."""
        _r, task_id, _q, answer = self._get_signup_page()
        response = self._post_signup(task_id, 99 if answer != 99 else answer + 1)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newuser").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_answer_then_correct_retry(self):
        """Д56: неверный ответ отклонён, затем правильный ответ на СВЕЖЕЕ
        задание перерендера проходит — легальный пользователь не потерян."""
        _r, task_id, _q, answer = self._get_signup_page()
        wrong = self._post_signup(
            task_id,
            99 if answer != 99 else answer - 1,
            email="retry@example.test",
            username="retryuser",
        )
        self.assertEqual(wrong.status_code, 200)  # форма с ошибкой
        self.assertFalse(User.objects.filter(username="retryuser").exists())
        # Перерендер выдаёт свежее задание (старое не гасится при ошибке)
        _r2, task2, _q2, answer2 = self._get_signup_page()
        self.assertIsNotNone(task2)
        ok = self._post_signup(
            task2, answer2, email="retry@example.test", username="retryuser"
        )
        self.assertEqual(ok.status_code, 302)
        self.assertTrue(User.objects.filter(username="retryuser").exists())

    def test_expired_math_task_rejected(self):
        """Просроченное/отсутствующее в кэше задание -> пользователя нет."""
        _r, task_id, _q, answer = self._get_signup_page()
        caches[MATH_CACHE].delete(f"signup-math:{task_id}")  # эмуляция истёкшего TTL
        response = self._post_signup(task_id, answer)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newuser").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_reused_math_task_rejected(self):
        """Повторное использование задания -> отклонено, дублей нет."""
        _r, task_id, _q, answer = self._get_signup_page()
        first = self._post_signup(task_id, answer)
        self.assertEqual(first.status_code, 302)
        second = self._post_signup(
            task_id, answer, email="dup@example.test", username="dupuser"
        )
        self.assertEqual(second.status_code, 200)  # форма с ошибкой "уже использовано"
        self.assertFalse(User.objects.filter(username="dupuser").exists())

    def test_rate_limiter_counts_requests_deterministically(self):
        """Детерминированный тест лимитера (Д54/Д60): прямые вызовы consume.
        Семантика allauth: 1) consume() коротко замыкает GET (limit_get=
        False) — хит только на POST; 2) в 65.21.1+ cache-ключ включает
        str(rate) — ставки ведут раздельные истории, лимит номинальный
        (10 POST/10м); в 65.20.1 ставки писали в общую историю (2 хита/POST
        -> 5 POST/10м). Эффективное значение — EFFECTIVE_POST_LIMIT выше.
        лимит достигается на POST №(EFFECTIVE_POST_LIMIT+1); другой IP
        не затронут; очистка истории (в проде — истечение окна) снимает
        ограничение."""
        from django.test import RequestFactory
        from allauth.core import ratelimit

        rf = RequestFactory()
        ip = "198.51.100.77"

        def request(method, addr):
            if method == "post":
                return rf.post("/accounts/signup/", REMOTE_ADDR=addr)
            return rf.get("/accounts/signup/", REMOTE_ADDR=addr)

        # GET не расходует счётчик: consume() возвращает True (пустой
        # usage), но хит не записан — иначе ниже лимит-POST был бы отказом.
        self.assertTrue(ratelimit.consume(request("get", ip), action="signup"))
        # EFFECTIVE_POST_LIMIT POST подряд — разрешены (зависит от версии
        # allauth: 65.20.1 -> 5, 65.21.1+ -> 10; см. константу выше)
        for i in range(EFFECTIVE_POST_LIMIT):
            self.assertTrue(
                ratelimit.consume(request("post", ip), action="signup"),
                f"POST #{i+1} не должен ограничиваться",
            )
        # следующий POST с того же IP — лимит
        self.assertFalse(ratelimit.consume(request("post", ip), action="signup"))
        # другой IP — не затронут
        self.assertTrue(
            ratelimit.consume(request("post", "203.0.113.200"), action="signup")
        )
        # очистка истории окна (в проде — истечение 10m) -> снова разрешено
        caches["default"].clear()
        self.assertTrue(ratelimit.consume(request("post", ip), action="signup"))

    def test_legit_signup_from_other_ip_works(self):
        """Легальная регистрация с другого IP не блокируется."""
        _r, task_id, _q, answer = self._get_signup_page()
        payload_email = "other-ip@example.test"
        response = self.client.post(
            SIGNUP_URL,
            {
                "email": payload_email,
                "username": "other-ip-user",
                "password1": "Strong-Pass-9x7",
                "password2": "Strong-Pass-9x7",
                "company": "",
                "math_token": task_id,
                "math_answer": str(answer),
                "parent": "",
            },
            REMOTE_ADDR="198.51.100.99",
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="other-ip-user").exists())

    def test_existing_user_login_unaffected(self):
        """Обычный вход существующего пользователя не затронут защитой."""
        login = self.client.login(username="olduser", password="old-pass-123")
        self.assertTrue(login)
        # allauth редиректит залогиненного со страницы регистрации (302) —
        # штатное поведение, защита регистрации на него не влияет.
        response = self.client.get(SIGNUP_URL)
        self.assertEqual(response.status_code, 302)

    def test_fresh_task_per_render(self):
        """Каждый рендер формы получает СВЕЖЕЕ задание (Д52: лишних нет):
        GET -> задание A; POST с ошибкой -> задание B (перегенерация);
        повторный POST с ошибкой -> задание C. Старые истекают по TTL."""
        ids = []
        for attempt in range(3):
            r = self.client.get(SIGNUP_URL, REMOTE_ADDR="203.0.113.88")
            html = r.content.decode()
            m_token = re.search(r'name="math_token"[^>]*value="([^"]+)"', html)
            self.assertTrue(m_token, f"попытка {attempt}: нет токена")
            ids.append(m_token.group(1))
            # POST с неверным ответом -> форма перерисовывается с ошибкой
            m_q = re.search(r'for="id_math_answer"[^>]*>([\s\S]*?)</label>', html)
            q_txt = re.sub(r"<[^>]+>", " ", m_q.group(1)) if m_q else ""
            q = re.search(r"(\d+)\s*([+-])\s*(\d+)", q_txt)
            a, op, b = int(q.group(1)), q.group(2), int(q.group(3))
            wrong = (a + b + 1) % 10
            self.client.post(
                SIGNUP_URL,
                {
                    "email": f"x{attempt}@example.test",
                    "username": f"x{attempt}",
                    "password1": "Xy12345678",
                    "password2": "Xy12345678",
                    "company": "",
                    "math_token": m_token.group(1),
                    "math_answer": str(wrong),
                    "parent": "",
                },
                REMOTE_ADDR="203.0.113.88",
            )
        # все три id различны — каждое задание свежее, дублей нет
        self.assertEqual(len(set(ids)), len(ids), f"дубли заданий: {ids}")


    def test_confirmation_email_sent_for_valid_signup(self):
        """Письмо подтверждения уходит (locmem) — без изменения почтовых настроек."""
        _r, task_id, _q, answer = self._get_signup_page()
        self._post_signup(task_id, answer, email="confirm-check@example.test", username="confirm-check")
        subjects = [m.subject for m in mail.outbox]
        self.assertTrue(any("подтверд" in s.lower() for s in subjects), subjects)

    def test_honeypot_field_hidden_in_markup(self):
        """Honeypot скрыт: офскрин-стиль, tabindex=-1, aria-hidden."""
        _r, task_id, _q, answer = self._get_signup_page()
        html = self.client.get(SIGNUP_URL).content.decode()
        self.assertIn('aria-hidden="true"', html)
        self.assertIn('tabindex="-1"', html)
        self.assertIn("left:-10000px", html)


class MathAnswerNonDisclosureTests(TestCase):
    """Д52: ответ задания не раскрывается клиенту ни в какой форме."""

    @classmethod
    def setUpTestData(cls):
        pass

    def setUp(self):
        caches["default"].clear()
        caches["security"].clear()
        self.client = self.client_class(REMOTE_ADDR="203.0.113.60")

    def _page(self):
        return self.client.get(SIGNUP_URL)

    def test_answer_absent_from_html(self):
        """Известный ответ отсутствует в HTML страницы регистрации."""
        _r, task_id, _q, answer = _get_signup_page(self.client)
        self.assertIsNotNone(answer)
        html = self._page().content.decode()
        self.assertNotIn(f"= {answer}", html)  # ответ не напечатан как результат
        # сам вопрос НЕ содержит ответ в читаемом виде (знаки есть, ответ нет)
        self.assertNotIn(f"= {answer}", html)

    def test_answer_absent_from_hidden_field_value(self):
        """Скрытое поле содержит только непрозрачный id, не ответ."""
        _r, task_id, _q, answer = _get_signup_page(self.client)
        html = self._page().content.decode()
        m = re.search(r'name="math_token"[^>]*value="([^"]*)"', html)
        self.assertTrue(m, "hidden input math_token отсутствует")
        value = m.group(1)
        self.assertNotEqual(value, str(answer))  # hidden = opaque id, не ответ
        self.assertNotIn("answer", value)
        # id — непрозрачный: base64url без '.'-разделителей подписи
        self.assertNotIn(".", value)

    def test_answer_absent_from_url(self):
        """Ответ не попадает в URL (GET-параметры)."""
        _r, task_id, _q, answer = _get_signup_page(self.client)
        response = self._page()
        self.assertNotIn(str(answer), response.request["QUERY_STRING"])
        self.assertEqual(response.request["QUERY_STRING"], "")

    def test_token_is_not_signed_answer_container(self):
        """В hidden-поле не попадает подписанный контейнер с ответом:
        значение не декодируется signing-ом как задание с answer."""
        _r, task_id, _q, answer = _get_signup_page(self.client)
        with self.assertRaises(dj_signing.BadSignature):
            dj_signing.loads(task_id)


class ParallelMathTaskTests(TestCase):
    """Атомарность одноразовости задания при параллельных отправках."""

    def test_consume_math_task_atomic(self):
        """Два потока гасят одно задание — успех только у одного."""
        import threading

        task_id = "mt_parallel_test"
        caches[MATH_CACHE].set(
            f"signup-math:{task_id}", {"question": "2 + 3 = ?", "answer": 5}, 60
        )
        results = []

        def worker():
            results.append(consume_math_task(task_id))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(True), 1, f"результаты: {results}")

    def test_consume_math_task_second_call_rejected(self):
        """Последовательный повтор: первый True, второй False."""
        task_id = "mt_seq_test"
        caches[MATH_CACHE].set(
            f"signup-math:{task_id}", {"question": "2 + 3 = ?", "answer": 5}, 60
        )
        self.assertTrue(consume_math_task(task_id))
        self.assertFalse(consume_math_task(task_id))
        caches[MATH_CACHE].delete(f"signup-math:used:{task_id}")


class RateLimitSemanticsTests(TestCase):
    """Д54/Д60: семантика лимита регистрации (сверена с исходником
    allauth обеих версий).

    @rate_limit(action="signup") висит на SignupView.dispatch, НО
    core/internal/ratelimit.py: consume() коротко замыкает GET
    (`if (not limit_get) and request.method == "GET": return usage`;
    limit_get=False по умолчанию) — счётчик расходует только POST.
    Ключ = allauth:rl:allauth.account:signup:ip:<IP>[:<rate>] в кэше
    default (FileBasedCache, общий для воркеров); IP —
    get_adapter().get_client_ip (X-Forwarded-For не доверяется).
    Фактический лимит зависит от версии allauth (EFFECTIVE_POST_LIMIT
    в начале модуля): 65.21.1+ — ключ включает str(rate), истории ставок
    раздельны, лимит номинальный 10 POST/10м (и 30/сутки); 65.20.1 —
    общая история, POST = 2 хита, 5 POST/10м. GET бесплатен в обеих.
    """

    def setUp(self):
        caches["default"].clear()
        caches["security"].clear()
        mail.outbox = []
        self.client = self.client_class(REMOTE_ADDR="203.0.113.70")

    def _get(self, ip="203.0.113.70"):
        return self.client.get(SIGNUP_URL, REMOTE_ADDR=ip).status_code

    def _post(self, i, ip="203.0.113.70"):
        html = self.client.get(SIGNUP_URL, REMOTE_ADDR=ip).content.decode()
        m_token = re.search(r'name="math_token"[^>]*value="([^"]+)"', html)
        m_q = re.search(r'for="id_math_answer"[^>]*>([\s\S]*?)</label>', html)
        question = re.sub(r"<[^>]+>", " ", m_q.group(1)) if m_q else ""
        q = re.search(r"(\d+)\s*([+-])\s*(\d+)", question)
        payload = {
            "email": f"rl{i}@example.test",
            "username": f"rl{i}",
            "password1": "Xy12345678",
            "password2": "Xy12345678",
            "company": "",
            "parent": "",
        }
        if m_token and q:
            payload["math_token"] = m_token.group(1)
            a, op, b = int(q.group(1)), q.group(2), int(q.group(3))
            payload["math_answer"] = str(a + b if op == "+" else a - b)
        return self.client.post(SIGNUP_URL, payload, REMOTE_ADDR=ip).status_code

    def test_get_requests_do_not_consume_limit(self):
        """GET не расходует лимит регистрации (фактическая семантика
        allauth для signup: счётчик — только на POST); 11 GET -> 200."""
        statuses = [self._get() for _ in range(11)]
        self.assertEqual(statuses, [200] * 11)

    def test_post_requests_consume_limit(self):
        """POST расходует лимит: EFFECTIVE_POST_LIMIT успешных POST
        с одного IP -> далее 429 (лимит зависит от версии allauth,
        см. константу в начале модуля)."""
        statuses = []
        for i in range(EFFECTIVE_POST_LIMIT + 6):
            html = self.client.get(SIGNUP_URL, REMOTE_ADDR="203.0.113.71").content.decode()
            m_token = re.search(r'name="math_token"[^>]*value="([^"]+)"', html)
            m_q = re.search(r'for="id_math_answer"[^>]*>([\s\S]*?)</label>', html)
            question = re.sub(r"<[^>]+>", " ", m_q.group(1)) if m_q else ""
            q = re.search(r"(\d+)\s*([+-])\s*(\d+)", question)
            payload = {
                "email": f"pl{i}@example.test",
                "username": f"pl{i}",
                "password1": "Xy12345678",
                "password2": "Xy12345678",
                "company": "",
                "parent": "",
            }
            if m_token and q:
                payload["math_token"] = m_token.group(1)
                a, op, b = int(q.group(1)), q.group(2), int(q.group(3))
                payload["math_answer"] = str(a + b if op == "+" else a - b)
            statuses.append(
                self.client.post(
                    SIGNUP_URL, payload, REMOTE_ADDR="203.0.113.71"
                ).status_code
            )
        self.assertEqual(statuses[:EFFECTIVE_POST_LIMIT], [302] * EFFECTIVE_POST_LIMIT)
        self.assertEqual(statuses[EFFECTIVE_POST_LIMIT:], [429] * 6)


class CascadeFlushMixin:
    """Локальная копия FlushCascadeMixin (blog.tests_replay_http): легаси-
    таблица blog_comment вне sqlflush, стандартный flush TransactionTestCase
    на postgres падает без CASCADE."""

    def _fixture_teardown(self):
        call_command(
            "flush", verbosity=0, interactive=False,
            database=self._databases_names()[0],
            reset_sequences=False, allow_cascade=True,
        )


class TwoProcessConsumeTests(CascadeFlushMixin, TransactionTestCase):
    """Д56: межпроцессная атомарность used-маркера на БОЕВОМ backend.

    В сюите (settings.test) alias persistent — LocMemCache: LocMem.add()
    атомарен внутри процесса внутренним локом, но это не боевой путь.
    Тест конструирует DatabaseCache напрямую (боевой класс + таблица
    blog_persistent_cache в тестовой БД) и гасит ОДНО задание из ДВУХ
    независимых процессов (multiprocessing, fork). Ровно один True —
    гарантия СУБД (PK-констрейнт + transaction.atomic, Д55/Д56).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # В settings.test нет DatabaseCache-алиасов, createcachetable пуст:
        # строим таблицу явно тем же DDL, что создаёт createcachetable для
        # DatabaseCache (боевая схема: cache_key = PRIMARY KEY). Transaction-
        # TestCase: DDL коммитится и виден дочерним процессам.
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS blog_persistent_cache ("
                "cache_key varchar(255) NOT NULL PRIMARY KEY, "
                "value text NOT NULL, "
                "expires timestamptz NOT NULL)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS blog_persistent_cache_expires "
                "ON blog_persistent_cache (expires)"
            )

    def test_two_processes_exactly_one_winner(self):
        import multiprocessing as mp

        ctx = mp.get_context("fork")  # ребёнок наследует настройки тест-БД
        # Закрываем родительский коннект ДО fork: ребёнок наследует закрытый
        # fd и открывает СВОИ соединения. Обратный порядок (close в ребёнке)
        # шлёт TLS-close в общий сокет и ломает родительский коннект
        # ("SSL connection has been closed unexpectedly" на flush).
        connection.close()

        def worker(task_id, barrier, out_q):
            try:
                # fork копирует открытый SSL-коннект родителя в thread-local:
                # без close_all ребёнок и родитель пишут в один сокет
                # (SSL "decryption failed"/"EOF detected"). Закрываем в
                # ребёнке — в родителе соединение остаётся рабочим.
                from django.db import connections

                connections.close_all()
                barrier.wait(timeout=10)  # оба стартуют одновременно
                # БОЕВОЙ класс бэкенда, боевая таблица (Д56)
                from django.core.cache.backends.db import DatabaseCache

                cache = DatabaseCache(
                    "blog_persistent_cache",
                    {"MAX_ENTRIES": 20000, "CULL_FREQUENCY": 8},
                )
                out_q.put(bool(cache.add(f"signup-math:used:{task_id}", True, 1800)))
            except Exception as exc:  # noqa: BLE001 — ошибка ребёнка должна
                out_q.put(f"ERR:{type(exc).__name__}: {exc}")  # завершить тест

        for round_i in range(3):
            task_id = f"mt_d56_2proc_{round_i}"
            barrier = ctx.Barrier(2)
            out_q = ctx.Queue()
            procs = [
                ctx.Process(target=worker, args=(task_id, barrier, out_q))
                for _ in range(2)
            ]
            for p in procs:
                p.start()
            results = [out_q.get(timeout=30) for _ in range(2)]
            for p in procs:
                p.join(timeout=15)
            errs = [r for r in results if isinstance(r, str)]
            self.assertEqual(errs, [], f"раунд {round_i}: ошибки процессов")
            self.assertEqual(
                sum(results), 1,
                f"раунд {round_i}: ровно один процесс должен погасить задание, "
                f"got: {results}",
            )


def ip_helper(i):
    return f"203.0.114.{i % 200}"
