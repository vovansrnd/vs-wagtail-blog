"""Д68-Б: конфигурация проверки «лишний DB alias не участвует в тестах».

Наследует тестовые настройки и добавляет алиас extra, указывающий на
реальную (не тестовую) БД — имитация settings.local с дополнительным
подключением, которое не используется ни одним тестом. Запуск:

    DJANGO_SETTINGS_MODULE=vsproject.settings.test_extra_alias \\
    EXTRA_DB_NAME=<реальная БД> EXTRA_DB_USER=<пользователь> \\
    EXTRA_DB_PASSWORD=<пароль> \\
    manage.py test <метки> --noinput

manage.py уважает явно заданный DJANGO_SETTINGS_MODULE (проверка переменной
стоит ДО setdefault), поэтому этот модуль подключается без правок manage.py.

Что доказывает прогон с этим модулем (см. vsproject/test_runner.py):
- Django 6.0.9 пропускает setup неиспользуемых алиасов
  (DiscoverRunner.get_databases: "Skipping setup of unused database(s)"),
  поэтому extra не переключается на тестовую БД и в old_config не попадает;
- SiteTestRunner не создаёт Django Site "testserver" в реальной extra-БД;
- раннер не выполняет pg_terminate_backend вовсе (удалён в Д68-Б);
- данные реальной extra-БД (канарейка) остаются неизменными.
"""
import os

from .test import *  # noqa: F401,F403

DATABASES = dict(DATABASES)
DATABASES["extra"] = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.environ.get("EXTRA_DB_NAME", "d68_extra_real"),
    "USER": os.environ.get("EXTRA_DB_USER", "d68_extra_real"),
    "PASSWORD": os.environ.get("EXTRA_DB_PASSWORD", ""),
    "HOST": os.environ.get("EXTRA_DB_HOST", "localhost"),
    "PORT": os.environ.get("EXTRA_DB_PORT", "5432"),
}
