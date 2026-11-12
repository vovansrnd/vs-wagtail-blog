"""Production settings.

Secrets and database credentials are supplied by the deployment environment.
This module deliberately does not import settings.local: doing so would switch
production back to DEBUG=True and replace its database/cache configuration.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        raise ImproperlyConfigured(
            f"The {name} environment variable is required by production settings."
        )
    return value


# Never commit a production SECRET_KEY to source control.
SECRET_KEY = _required_env("DJANGO_SECRET_KEY")

# Explicit production host allowlist, including the www aliases configured by
# the project. Deployments with different hostnames can override it via env.
_production_allowed_hosts = [
    "vs-svet.ru", "www.vs-svet.ru",
    "floppygames.ru", "www.floppygames.ru",
    "zenway.ru", "www.zenway.ru",
]
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", ",".join(_production_allowed_hosts)
    ).split(",")
    if host.strip()
]

# Use explicit production database credentials; local.py is development-only.
DATABASES = {
    "default": {
        "ENGINE": os.environ.get(
            "DB_ENGINE", "django.db.backends.postgresql"
        ),
        "NAME": _required_env("DB_NAME"),
        "USER": _required_env("DB_USER"),
        "PASSWORD": _required_env("DB_PASSWORD"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

# Легаси-алиас "legacy" (MySQL эпохи WordPress) УДАЛЁН (2026-11-05, решение
# владельца): env-условный блок воскресал настройки при каждом деплое, а сама
# БД миграции давно не нужна. История: git log -S "LEGACY_DB_NAME".

# Mail transport is configured in settings.base (with optional host-local
# overrides from settings.local_secrets). This module intentionally does not
# switch SMTP/Sendmail backends. Historical comments about blocked SMTP ports
# and a tested Sendmail path were not an accurate description of active code.

SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Enable only if every subdomain covered by the policy supports HTTPS.
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", "false"
).strip().lower() in {"1", "true", "yes", "on"}

# Django receives the visitor IP directly on this hosting; do not trust client-supplied XFF.
COMMENTS_TRUST_PROXY_HEADERS = False
COMMENTS_TRUSTED_PROXY_IPS = ()

