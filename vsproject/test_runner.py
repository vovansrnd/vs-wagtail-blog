"""Тестовый раннер Д68: Django Site для Host тестового клиента + диагностика
teardown, строго в пределах тестовых БД ТЕКУЩЕГО запуска.

CurrentSiteMiddleware (django.contrib.sites) резолвит сайт по Host запроса.
Django-тестовый клиент ходит с Host "testserver"; в чистой установке среди
записей django.contrib.sites.models.Site его нет, а SITE_ID глобально
сознательно не задаётся: многосайтовый проект выбирает сайт по Host, и
фиксированная запись в общих настройках подменяла бы этот выбор для всех
доменов. Запись создаёт ТЕСТОВЫЙ раннер. Это инфраструктура тестов:
dev/production настройки, wagtail.models.Site и маршрутизация трёх доменов
не затрагиваются.

Область действия (Д68-Б, замечание координатора — блокирующее):
все записи и диагностика ограничены соединениями, которые ЭТОТ запуск
переключил на тестовые БД. Список берётся из old_config — значения,
возвращаемого django.test.utils.setup_databases. Проверено по исходникам
Django 6.0.9 (django/test/utils.py, django/test/runner.py):
- run_tests передаёт setup_databases aliases=get_databases(suite) — только
  алиасы, используемые найденными тестами; get_databases явно пропускает
  неиспользуемые ("Skipping setup of unused database(s)"), и их соединения
  продолжают смотреть в РЕАЛЬНЫЕ БД;
- old_config — список (connection, original_name, is_primary) ровно для
  переключённых соединений: первичный получил create_test_db, остальные
  с той же сигнатурой настроены set_as_test_mirror на тестовую БД
  первичного; алиасы с TEST["MIRROR"] в old_config отсутствуют — они тоже
  направлены на тестовую БД своего зеркала.
Поэтому алиас, настроенный в settings.local, но не используемый тестами,
в old_config не попадает — раннер не обращается к нему вовсе.

Подключение: TEST_RUNNER в vsproject/settings/test.py. Конфигурация
проверки области действия: vsproject/settings/test_extra_alias.py.
"""
from django.test.runner import DiscoverRunner

TEST_CLIENT_HOST = "testserver"


class SiteTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        from django.contrib.sites.models import Site

        for connection, original_name, is_primary in old_config:
            # Запись создаёт первичный алиас каждой созданной тестовой БД
            # (вторичные той же сигнатуры смотрят на ту же тестовую БД).
            if not is_primary:
                continue
            test_name = connection.settings_dict["NAME"]
            if test_name == original_name:
                # Защита в глубину: соединение не переключено на тестовую БД —
                # писать в него нельзя ни при каких условиях.
                print(f"[test_runner] alias {connection.alias!r} still points "
                      f"at {original_name!r} (not switched); testserver skip")
                continue
            Site.objects.using(connection.alias).get_or_create(
                domain=TEST_CLIENT_HOST,
                defaults={"name": f"{TEST_CLIENT_HOST} (Django test client)"},
            )
        return old_config

    def teardown_databases(self, old_config):
        try:
            self._print_foreign_sessions(old_config)
        except Exception as exc:  # noqa: BLE001 — диагностика не маскирует вердикт
            print(f"[test_runner] teardown diagnostics failed: "
                  f"{type(exc).__name__}: {exc}")
        return super().teardown_databases(old_config)

    def _print_foreign_sessions(self, old_config):
        """Read-only диагностика посторонних сессий ТОЛЬКО тестовых БД
        текущего запуска (PostgreSQL).

        pg_terminate_backend НЕ выполняется (Д68-Б): причина ObjectInUse не
        установлена — три чистых прогона после исправления тестов прошли
        без рецидива, и автоматическое завершение посторонних подключений
        не обосновано. Если ObjectInUse повторится, эта диагностика назовёт
        pid, состояние и последний запрос виновной сессии — тогда решение
        о завершении будет предметным и адресным.
        """
        for connection, original_name, is_primary in old_config:
            if not is_primary or connection.vendor != "postgresql":
                continue
            test_name = connection.settings_dict["NAME"]
            if test_name == original_name:
                continue  # не переключено — не диагностируем
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pid, state, query FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    [test_name],
                )
                stragglers = cursor.fetchall()
            for pid, state, query in stragglers:
                snippet = (query or "").strip().replace("\n", " ")[:120]
                print(f"[test_runner] foreign session on test DB {test_name!r}: "
                      f"pid={pid} state={state} last_query={snippet!r}")
