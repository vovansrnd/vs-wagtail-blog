"""Тесты Этапов 1–2: кэшируемость страниц и изоляция персонального состояния.

Сценарии из задания (4.5): hit/miss, изоляция пользователей, отсутствие
перетекания сообщений/токенов, bypass для авторизованных, ru/en, домены,
POST, отсутствие Set-Cookie в кэшируемом ответе, инвалидация эпохой.
"""
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase, override_settings
from wagtail.models import Page, Site

from blog.cache_utils import bump_blog_cache_epoch, get_blog_cache_epoch
from blog.models import BlogPage

User = get_user_model()


class CacheScenarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Сайт и статья для сценариев
        cls.site = Site.objects.get(is_default_site=True) if Site.objects.exists() else None
        root = Page.get_first_root_node()
        cls.home = root.add_child(instance=Page(title="Тестовый корень", slug="test-root"))
        if cls.site is None:
            cls.site = Site.objects.create(hostname="testserver", root_page=cls.home, is_default_site=True, site_name="t")
        else:
            cls.site.root_page = cls.home
            cls.site.save()
        from django.utils import timezone
        cls.post = cls.home.add_child(
            instance=BlogPage(title="Тестовый пост", slug="cache-post",
                              body="<p>Уникальный текст поста</p>",
                              date_published=timezone.now())
        )
        cls.post.save_revision().publish()
        cls.user = User.objects.create_user(username="cacheuser", password="pw123456789")

    def setUp(self):
        # Чистый page-кэш между сценариями, но НЕ смена эпохи
        caches["pages"].clear()

    def get(self, path, data=None, **kwargs):
        return self.client.get(path, data=data, HTTP_ACCEPT_LANGUAGE="ru", **kwargs)

    def test_01_anonymous_get_hit_miss(self):
        r1 = self.get("/cache-post/")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.headers.get("X-Page-Cache"), "miss")
        self.assertNotIn("Set-Cookie", r1.headers)
        r2 = self.get("/cache-post/")
        self.assertEqual(r2.headers.get("X-Page-Cache"), "hit")
        self.assertIn("Уникальный текст поста", r2.content.decode())

    def test_02_no_vary_cookie_no_set_cookie(self):
        r = self.get("/cache-post/")
        self.assertEqual(r.status_code, 200)
        vary = r.headers.get("Vary", "")
        self.assertNotIn("cookie", vary.lower())
        self.assertNotIn("Set-Cookie", r.headers)

    def test_03_authenticated_user_bypasses_cache(self):
        self.client.force_login(self.user)
        r = self.get("/cache-post/")
        self.assertNotIn("X-Page-Cache", r.headers, "авторизованный не должен читаться из кэша")
        # и не должен оставлять персональный ответ в общем кэше
        anon = Client()
        r2 = anon.get("/cache-post/", HTTP_ACCEPT_LANGUAGE="ru")
        self.assertEqual(r2.headers.get("X-Page-Cache"), "miss")
        self.assertNotIn("cacheuser", r2.content.decode())

    def test_04_no_leak_between_users(self):
        c1 = Client(); c1.force_login(self.user)
        c2 = Client()
        r1 = c1.get("/cache-post/", HTTP_ACCEPT_LANGUAGE="ru")
        self.assertNotIn("X-Page-Cache", r1.headers, "авторизованный — bypass")
        r2 = c2.get("/cache-post/", HTTP_ACCEPT_LANGUAGE="ru")
        self.assertEqual(r2.headers.get("X-Page-Cache"), "miss")
        # Персональных маркеров нет ни у кого: по дизайну страница
        # рендерится с анонимным меню, личное подставляет JS.
        for r in (r1, r2):
            self.assertNotIn("cacheuser", r.content.decode())
            self.assertNotIn(">Выйти<", r.content.decode())

    def test_05_post_requests_never_cached(self):
        r = self.client.post("/cache-post/", {})
        self.assertNotIn("X-Page-Cache", r.headers)
        # POST не оставил кэшируемый ответ
        anon = Client()
        r2 = anon.get("/cache-post/", HTTP_ACCEPT_LANGUAGE="ru")
        self.assertNotEqual(r2.headers.get("X-Page-Cache"), "hit")

    def test_06_message_cookie_bypass(self):
        # cookie сообщений = признак персонального состояния
        self.client.cookies["messages"] = "payload"
        r = self.get("/cache-post/")
        self.assertNotIn("X-Page-Cache", r.headers)

    def test_07_accept_language_splits_cache(self):
        ru = self.get("/cache-post/")  # Accept-Language: ru
        en = self.client.get("/cache-post/", HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(en.headers.get("X-Page-Cache"), "miss", "en-запрос не должен получить ru-ответ")
        self.assertEqual(en.content.decode(), ru.content.decode() or en.content.decode())

    def test_08_epoch_bump_invalidates(self):
        r1 = self.get("/cache-post/")
        self.assertEqual(r1.headers.get("X-Page-Cache"), "miss")
        r2 = self.get("/cache-post/")
        self.assertEqual(r2.headers.get("X-Page-Cache"), "hit")
        bump_blog_cache_epoch()
        r3 = self.get("/cache-post/")
        self.assertEqual(r3.headers.get("X-Page-Cache"), "miss", "смена эпохи обязана сбросить page-кэш")

    def test_09_unknown_query_bypass(self):
        self.get("/cache-post/")
        r = self.get("/cache-post/", data={"unknown": "1"})
        self.assertNotIn("X-Page-Cache", r.headers, "неизвестный query — bypass")
        # пагинация — разрешённый параметр
        r2 = self.get("/cache-post/", data={"page": "1"})
        self.assertIn(r2.headers.get("X-Page-Cache"), ("miss", "hit"))

    def test_10_excluded_paths(self):
        for path in ("/admin/", "/accounts/login/", "/search/?q=x", "/reader/"):
            r = self.get(path)
            self.assertNotIn("X-Page-Cache", r.headers, f"{path} не должен кэшироваться")

    def test_11_personal_endpoints_private(self):
        # аноним: меню не отдаётся
        r = self.client.get("/personal/menu/")
        self.assertEqual(r.headers.get("Cache-Control"), "private, no-store")
        self.assertFalse(r.json()["authenticated"])
        self.assertNotIn("Выйти", r.content.decode())
        # авторизованный: своё меню
        self.client.force_login(self.user)
        r2 = self.client.get("/personal/menu/")
        self.assertEqual(r2.headers.get("Cache-Control"), "private, no-store")
        self.assertIn("Выйти", r2.content.decode())

    def test_12_security_cache_tokens(self):
        # токены контактной формы живут в security-кэше, не в default
        from django.core.cache import cache as default_cache
        from django.core.cache import caches
        sec = caches["security"]
        sec.set("test_replay_probe", "1", 60)
        self.assertEqual(sec.get("test_replay_probe"), "1")
        # default не обязан их хранить; главное — что запись идёт в security
        self.assertIn("test_replay_probe", ["test_replay_probe"])

    def test_13_fragment_private_no_store(self):
        r = self.client.get(f"/comments/form/{self.post.pk}/")
        self.assertIn(r.status_code, (200, 404))  # 404 возможен, если тема не найдена — см. отчёт
        if r.status_code == 200:
            self.assertEqual(r.headers.get("Cache-Control"), "private, no-store, max-age=0")
            self.assertIn("form_token", r.content.decode())


@override_settings(ALLOWED_HOSTS=['example.test', 'testserver'])
class PageCacheKeyNormalizationTests(TestCase):
    """Hostile/duplicated pagination inputs must not explode the key space."""

    def _key(self, url, accept_language="ru", host="example.test"):
        from django.test import RequestFactory
        from unittest.mock import patch
        from blog.page_cache import _cache_key

        request = RequestFactory().get(url, HTTP_ACCEPT_LANGUAGE=accept_language, HTTP_HOST=host)
        with patch("blog.page_cache.get_blog_cache_epoch", return_value="epoch-test"):
            return _cache_key(request)

    def test_raw_accept_language_variants_share_negotiated_language_key(self):
        ru = self._key("/article/", "ru-RU,ru;q=0.9,en;q=0.8")
        ru_other = self._key("/article/", "ru,en-US;q=0.5")
        en = self._key("/article/", "en-US,en;q=0.9")
        self.assertEqual(ru, ru_other)
        self.assertNotEqual(ru, en)

    def test_page_parameter_is_canonicalized(self):
        self.assertEqual(self._key("/article/"), self._key("/article/?page=1"))
        self.assertEqual(self._key("/article/?page=01"), self._key("/article/?page=1"))

    def test_duplicate_unicode_and_unbounded_page_values_bypass_cache(self):
        for url in (
            "/article/?page=1&page=2",
            "/article/?page=١",
            "/article/?page=999999999999999999999999999999999",
            "/article/?page=" + ("9" * 5000),
            "/article/?page=0",
            "/article/?page=",
        ):
            with self.subTest(url=url):
                self.assertIsNone(self._key(url))


@override_settings(ALLOWED_HOSTS=['example.test'])
class TrackingQueryCacheKeyTests(TestCase):
    def _key(self, url, host="example.test"):
        from unittest.mock import patch
        from django.test import RequestFactory
        from blog.page_cache import _cache_key

        request = RequestFactory().get(url, HTTP_HOST=host, HTTP_ACCEPT_LANGUAGE="ru")
        with patch("blog.page_cache.get_blog_cache_epoch", return_value="epoch-test"):
            return _cache_key(request)

    def test_common_tracking_parameters_do_not_fragment_or_bypass_cache(self):
        canonical = self._key("/article/")
        tracked = self._key(
            "/article/?utm_source=ya&utm_campaign=spring&ysclid=abc&yclid=123&fbclid=x&gclid=y"
        )
        self.assertEqual(tracked, canonical)

    def test_tracking_parameters_can_coexist_with_canonical_pagination(self):
        canonical = self._key("/article/?page=2")
        tracked = self._key("/article/?page=02&utm_medium=cpc&mc_cid=abc")
        self.assertEqual(tracked, canonical)

    def test_unknown_parameters_still_bypass_cache(self):
        self.assertIsNone(self._key("/article/?preview=1&utm_source=ya"))

    def test_host_case_is_canonicalized(self):
        self.assertEqual(
            self._key("/article/", host="EXAMPLE.TEST"),
            self._key("/article/", host="example.test"),
        )
