import os

from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# Dev-only fallback: фиксированный ключ в исходниках позволяет подделать сессию.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-placeholder-key-not-for-production")

# SECURITY WARNING: define the correct hosts in production!
ALLOWED_HOSTS = ["*"]

#EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


try:
    from .local import *
except ImportError:
    pass
