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
        try:
            self._drain_and_clear(old_config)
        except Exception as exc:  # noqa: BLE001 — очистка не маскирует вердикт
            print(f"[test_runner] teardown drain failed: "
                  f"{type(exc).__name__}: {exc}")
        return super().teardown_databases(old_config)

    def _print_foreign_sessions(self, old_config):
        """Read-only диагностика посторонних сессий ТОЛЬКО тестовых БД
        текущего запуска (PostgreSQL).

        Улики Д68-Т: две утечки замечены этой диагностикой — idle-сессия с
        последним запросом SELECT к blog_invalidationepoch, живущая до
        выхода процесса (явление A), и ObjectInUse при пустой диагностике —
        гонка close→DROP, когда backend своего соединения ещё не завершился
        к моменту DROP DATABASE (явление B).
        """
        for connection, original_name, is_primary in old_config:
            if not is_primary or connection.vendor != "postgresql":
                continue
            test_name = connection.settings_dict["NAME"]
            if test_name == original_name:
                continue  # не переключено — не диагностируем
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pid, state, query, backend_start FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    [test_name],
                )
                stragglers = cursor.fetchall()
            for pid, state, query, backend_start in stragglers:
                snippet = (query or "").strip().replace("\n", " ")[:120]
                print(f"[test_runner] foreign session on test DB {test_name!r}: "
                      f"pid={pid} state={state} backend_start={backend_start} "
                      f"last_query={snippet!r}")

    def _drain_and_clear(self, old_config):
        """Детерминированная очистка тестовых БД текущего запуска.

        Для каждой тестовой БД из old_config (PostgreSQL, первичные алиасы):
        1) закрыть собственное соединение — его backend завершается
           асинхронно, и DROP сразу после close() попадает в гонку (явление
           B: ObjectInUse на собственном умирающем соединении);
        2) дождаться НУЛЯ сессий на тестовой БД (опрос из отдельного
           подключения к postgres, до 5 с);
        3) если сессии остались — это утечка соединения самого процесса
           (явление A: соединение живёт до выхода процесса): завершить их
           pg_terminate_backend и дождаться нуля.

        Границы: только тестовые БД ЭТОГО запуска (old_config), только
        teardown, только PostgreSQL. Сбой шага не маскирует вердикт сюита —
        исключение печатается, стандартный teardown выполняется как прежде.
        """
        import time

        import psycopg2

        for connection, original_name, is_primary in old_config:
            if not is_primary or connection.vendor != "postgresql":
                continue
            test_name = connection.settings_dict["NAME"]
            if test_name == original_name:
                continue
            settings = connection.settings_dict
            pg = psycopg2.connect(
                dbname="postgres", user=settings["USER"],
                password=settings["PASSWORD"], host=settings.get("HOST") or None,
                port=settings.get("PORT") or None, connect_timeout=10,
            )
            pg.set_session(autocommit=True)
            try:
                connection.close()  # наш backend начинает завершаться
                deadline = time.monotonic() + 5.0
                stragglers = self._sessions_on(pg, test_name)
                while stragglers and time.monotonic() < deadline:
                    time.sleep(0.05)
                    stragglers = self._sessions_on(pg, test_name)
                if stragglers:
                    print(f"[test_runner] {len(stragglers)} lingering session(s) "
                          f"on {test_name!r} after drain; terminating "
                          f"(утечка соединения процесса — явление A):")
                    for pid, state, query, backend_start in stragglers:
                        snippet = (query or "").strip().replace("\n", " ")[:120]
                        print(f"[test_runner]   terminating pid={pid} state={state} "
                              f"backend_start={backend_start} last_query={snippet!r}")
                        with pg.cursor() as cur:
                            cur.execute("SELECT pg_terminate_backend(%s)", [pid])
                    deadline = time.monotonic() + 5.0
                    stragglers = self._sessions_on(pg, test_name)
                    while stragglers and time.monotonic() < deadline:
                        time.sleep(0.05)
                        stragglers = self._sessions_on(pg, test_name)
                    if stragglers:
                        pids = [pid for pid, *_ in stragglers]
                        print(f"[test_runner] WARNING: sessions still present "
                              f"on {test_name!r}: {pids}")
                else:
                    zero_in = round(5.0 - (deadline - time.monotonic()), 1)
                    print(f"[test_runner] test DB {test_name!r} drained in "
                          f"~{zero_in}s sessions; DROP без гонки")
            finally:
                pg.close()

    @staticmethod
    def _sessions_on(pg, test_name):
        """Сессии на тестовой БД (опрос из подключения к postgres)."""
        with pg.cursor() as cur:
            cur.execute(
                "SELECT pid, state, query, backend_start FROM pg_stat_activity "
                "WHERE datname = %s",
                [test_name],
            )
            return cur.fetchall()
