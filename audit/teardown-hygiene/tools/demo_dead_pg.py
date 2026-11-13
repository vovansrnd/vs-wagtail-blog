"""Д68-досье: демонстрация отказа служебного подключения при дренаже.

Если служебное подключение к postgres недоступно (мертвый хост/порт,
нет прав), _drain_and_clear перехватывает исключение и печатает
"teardown drain failed", а стандартный teardown выполняется как прежде —
вердикт сюиты не маскируется. Сценарий имитирует недоступность через
настройки с мертвым портом.
"""
import django
from django.conf import settings

settings.configure(
    DEBUG=True, SECRET_KEY="demo", USE_TZ=True,
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
    DATABASES={"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "d68_dead_pg_demo", "USER": "a1020391_django_radio",
        "PASSWORD": "radio-local", "HOST": "127.0.0.1", "PORT": "5499",
    }},
)
django.setup()
from vsproject.test_runner import SiteTestRunner

runner = SiteTestRunner()

class _StubConn:
    """Заглушка соединения: vendor/настройки с мертвым портом.

    creation намеренно возбуждает исключение: в демо вызывается полный
    teardown_databases, и после перехвата ошибки дренажа штатный teardown
    демонстративно упирается в заглушку (реальный прогон продолжил бы его
    на настоящем соединении).
    """
    vendor = "postgresql"
    alias = "default"

    def __init__(self, settings_dict):
        self.settings_dict = settings_dict

    def close(self):
        pass

    @property
    def creation(self):
        raise RuntimeError("demo: super-teardown недоступен на заглушке")


fake_old_config = [(_StubConn(dict(settings.DATABASES["default"])),
                    "d68_dead_pg_demo_SOURCE", True)]
print("демо: дренаж при недоступном postgres (порт 5499)")
try:
    runner.teardown_databases(fake_old_config)
except Exception as exc:  # noqa: BLE001 — демо перехватывает остаточный отказ
    print(f"  [демо] штатный teardown завершился с "
          f"{type(exc).__name__}: {exc}")
print("ИТОГ: исключение дренажа перехвачено и напечатано выше; "
      "вердикт сюиты не маскируется, стандартный teardown продолжается")
