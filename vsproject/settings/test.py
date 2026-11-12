"""Настройки для `manage.py test`: наследует dev, но изолирует кэши.

Тестовый прогон с dev-настройками писал в общие filebased-кэши
(default/security/pages): в них оставались, например, устаревшие
wagtail_site_root_paths от тестовых сайтов, что ломало get_url()
на работающем dev-сервере. LocMem-кэши живут только внутри процесса
тестов и ничего не портят на диске.

Используется автоматически: manage.py переключает settings при
`manage.py test` (см. manage.py), либо DJANGO_SETTINGS_MODULE=vsproject.settings.test.
"""
from .dev import *  # noqa: F401,F403

CACHES = {
    alias: {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": f"test-{alias}",
    }
    for alias in ("default", "security", "pages", "persistent")
}

# Письма из тестов не должны уходить в sendmail/SMTP даже при ошибке
# логгера: locmem вместо любого smtp-бэкенда.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
