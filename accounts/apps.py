from django.apps import AppConfig
from django.conf import settings


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # Безопасные DB-сигналы: создание профилей и уведомления админов.
        from . import signals  # noqa: F401

        # Загрузка аватаров делает внешние HTTP-запросы прямо во время входа —
        # только явный opt-in, чтобы логин не зависел от внешних сервисов.
        if getattr(settings, "AVATAR_AUTO_FETCH_ON_LOGIN", False):
            from . import avatar_signals  # noqa: F401
