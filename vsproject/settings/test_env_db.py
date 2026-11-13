"""Д68-досье: тестовые настройки с env-управляемой БД БЕЗ settings.local.

Наследует base (DATABASES из DB_* env — как на проде) и изолирует кэши/почту
как test.py. Используется для сценария параллельных прогонов досье: второй
процесс получает СВОЮ тестовую БД (test_<DB_NAME>), не пересекаясь с первой.
"""
from .base import *  # noqa: F401,F403

CACHES = {
    alias: {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"test-env-{alias}"}
    for alias in ("default", "security", "pages", "persistent")
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Аудиторская метка: локальный прогон, без локальных секретов.
SECRET_KEY = "d68-parallel-audit-secret"

# Раннер проекта (метки запуска + дренаж teardown) — как в settings/test.py.
TEST_RUNNER = "vsproject.test_runner.SiteTestRunner"
