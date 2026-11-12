"""Тесты блокировки перебора паролей (Этап 3)."""
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase, override_settings

from blog.login_lockout import FAIL_LIMIT, _key, is_locked, security

User = get_user_model()


class LoginLockoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from allauth.account.models import EmailAddress
        cls.user = User.objects.create_user(username="lockuser", password="correct-horse")
        cls.admin = User.objects.create_superuser(
            username="lockadmin", email="lockadmin@test.local", password="correct-admin-horse"
        )
        # mandatory email verification: без подтверждённого адреса allauth
        # отклоняет вход формой до аутентификации.
        EmailAddress.objects.create(user=cls.user, email="lockuser@test.local",
                                    verified=True, primary=True)
        EmailAddress.objects.create(user=cls.admin, email="lockadmin@test.local",
                                    verified=True, primary=True)

    def setUp(self):
        caches["security"].clear()

    def _failed_post(self, ip="10.0.0.1", username="lockuser", password="wrong"):
        # /admin/login/ идёт через чистый authenticate() -> сигнал user_login_failed
        # (/accounts/login/ защищён собственным ratelimit allauth)
        return self.client.post(
            "/admin/login/",
            {"username": username, "password": password},
            REMOTE_ADDR=ip,
        )

    def test_correct_login_not_blocked(self):
        r = self.client.post(
            "/accounts/login/",
            {"login": "lockuser", "password": "correct-horse"},
            REMOTE_ADDR="10.0.0.2",
        )
        self.assertNotEqual(r.status_code, 429)
        self.assertTrue(self.client.session.get("_auth_user_id") is not None)

    def test_lockout_after_limit(self):
        for i in range(FAIL_LIMIT):
            r = self._failed_post(ip="10.0.0.1")
            self.assertNotEqual(r.status_code, 429, f"попытка {i + 1} не должна блокироваться")
        r = self._failed_post(ip="10.0.0.1")
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)
        # /accounts/login/ тем временем защищён allauth (его собственный лимит)
        r_acct = self.client.post(
            "/accounts/login/",
            {"login": "lockuser", "password": "wrong"},
            REMOTE_ADDR="10.0.0.1",
        )
        self.assertNotEqual(r_acct.status_code, 429)

    def test_lockout_is_per_ip(self):
        for i in range(FAIL_LIMIT):
            self._failed_post(ip="10.0.0.1")
        # другой IP — не заблокирован
        r = self._failed_post(ip="10.0.0.2")
        self.assertNotEqual(r.status_code, 429)
        # username не влияет: блокировка чисто по IP (user-key = DoS-вектор)
        r = self._failed_post(ip="10.0.0.1", username="other")
        self.assertEqual(r.status_code, 429)

    def test_regular_account_login_does_not_clear_admin_counter(self):
        for _ in range(FAIL_LIMIT - 1):
            self._failed_post(ip="10.0.0.3")
        ok = self.client.post(
            "/accounts/login/",
            {"login": "lockuser", "password": "correct-horse"},
            REMOTE_ADDR="10.0.0.3",
        )
        self.assertNotEqual(ok.status_code, 429)
        self.client.post("/accounts/logout/", REMOTE_ADDR="10.0.0.3")
        # Обычная авторизация не очистила счётчик: это десятая (ещё допустимая)
        # неудачная попытка, следующая уже блокируется.
        tenth = self._failed_post(ip="10.0.0.3")
        self.assertNotEqual(tenth.status_code, 429)
        eleventh = self._failed_post(ip="10.0.0.3")
        self.assertEqual(eleventh.status_code, 429)

    def test_successful_staff_admin_login_clears_counter(self):
        for _ in range(FAIL_LIMIT - 1):
            self._failed_post(ip="10.0.0.4")
        ok = self.client.post(
            "/admin/login/",
            {"username": "lockadmin", "password": "correct-admin-horse"},
            REMOTE_ADDR="10.0.0.4",
        )
        self.assertNotEqual(ok.status_code, 429)
        self.assertFalse(is_locked("10.0.0.4"))

    def test_is_locked_unit(self):
        self.assertFalse(is_locked("1.1.1.1"))
        security.set(_key("1.1.1.1"), [1] * 10, 60)
        self.assertTrue(is_locked("1.1.1.1"))
        security.set(_key("1.1.1.1"), [1] * 9, 60)
        self.assertFalse(is_locked("1.1.1.1"))
