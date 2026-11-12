"""Тесты изоляции тестового окружения (Этап 1 финальной стабилизации).

Гарантируют, что `manage.py test` работает на vsproject.settings.test:
LocMem-кэши вместо общих filebased и locmem-почта вместо SMTP/sendmail.

Инцидент-прототип: прогон тестов с dev-настройками писал в filebased-кэши
работающего сервера (cache/app, cache/security, cache/pages), оставляя там
мусор вроде wagtail_site_root_paths от тестовых сайтов, что ломало
page.get_url() и редиректы на живом сервере.
"""
from django.conf import settings
from django.core.mail import get_connection
from django.test import TestCase


class TestEnvironmentIsolationTests(TestCase):
    def test_all_cache_aliases_are_locmem(self):
        """Все используемые cache-алиасы — LocMemCache (изоляция от dev/prod)."""
        expected_aliases = {"default", "security", "pages", "persistent"}
        configured = set(settings.CACHES)
        self.assertEqual(
            configured, expected_aliases,
            f"набор cache-алиасов изменился: {configured}; обнови тест")
        for alias in sorted(expected_aliases):
            backend = settings.CACHES[alias]["BACKEND"]
            self.assertEqual(
                backend, "django.core.cache.backends.locmem.LocMemCache",
                f"кэш '{alias}' использует {backend}: тесты пишут в общие "
                "кэши dev/prod! Проверь переключение настроек в manage.py")

    def test_email_backend_is_locmem(self):
        """Тестовая почта — locmem, никаких SMTP/sendmail попыток."""
        self.assertEqual(
            settings.EMAIL_BACKEND,
            "django.core.mail.backends.locmem.EmailBackend",
            f"EMAIL_BACKEND={settings.EMAIL_BACKEND}: тесты могут уйти в SMTP")
        conn = get_connection()
        self.assertEqual(conn.__class__.__module__,
                         "django.core.mail.backends.locmem")

    def test_debug_settings_not_production(self):
        """Тесты не должны запускаться с production-настройками."""
        self.assertNotIn("production", settings.SETTINGS_MODULE)
        self.assertIn("test", settings.SETTINGS_MODULE)
