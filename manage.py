#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # Тесты не должны писать в общие filebased-кэши dev/prod-настроек:
    # ранее прогон оставлял там мусор (например, wagtail_site_root_paths
    # от тестовых сайтов) и ломал работающий сервер. См. settings/test.py.
    # ВАЖНО: проверка ДО setdefault — иначе setdefault("...", dev) уже
    # создаёт переменную, и тесты молча идут с dev-настройками.
    if "test" in sys.argv and "DJANGO_SETTINGS_MODULE" not in os.environ:
        os.environ["DJANGO_SETTINGS_MODULE"] = "vsproject.settings.test"
    else:
#        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vsproject.settings.production")
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vsproject.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
