"""Тестовый раннер Д68: Django Site для Host тестового клиента + очистка
тестовых БД, строго в пределах ТЕКУЩЕГО запуска и ЕГО соединений.

CurrentSiteMiddleware (django.contrib.sites) резолвит сайт по Host запроса.
Django-тестовый клиент ходит с Host "testserver"; в чистой установке среди
записей django.contrib.sites.models.Site его нет, а SITE_ID глобально
сознательно не задаётся: многосайтовый проект выбирает сайт по Host, и
фиксированная запись в общих настройках подменяла бы этот выбор для всех
доменов. Запись создаёт ТЕСТОВЫЙ раннер. Это инфраструктура тестов:
dev/production настройки, wagtail.models.Site и маршрутизация трёх доменов
не затрагиваются.

Область действия (Д68-Б, замечание координатора): все записи, диагностика
и очистка ограничены соединениями, которые ЭТОТ запуск переключил на
тестовые БД. Список берётся из old_config — значения, возвращаемого
django.test.utils.setup_databases. Проверено по исходникам Django 6.0.9:
run_tests передаёт aliases=get_databases(suite) — только алиасы, используемые
найденными тестами; неиспользуемые пропускаются ("Skipping setup of unused
database(s)") и их соединения продолжают смотреть в РЕАЛЬНЫЕ БД; old_config —
список (connection, original_name, is_primary) ровно для переключённых
соединений. Поэтому алиас из settings.local, не используемый тестами,
в old_config не попадает — раннер не обращается к нему вовсе.

Граница безопасности pg_terminate_backend (Д68-Т, замечание координатора —
блокирующее): имя тестовой БД доказывает отношение БД к запуску, но НЕ
принадлежность каждой подключённой сессии. Поэтому при setup раннер
проставляет во все алиасы OPTIONS["application_name"] с уникальной меткой
запуска (pid + случайный суффикс): каждое соединение, открытое ЭТИМ
процессом после setup, несёт эту метку. Дренаж teardown завершает ТОЛЬКО
сессии с меткой ЭТОГО запуска (кроме явно исключённых по pid). Сессии на
тестовой БД БЕЗ метки или с чужой меткой (параллельный запуск тестов,
диагностика разработчика) НЕ завершаются — они диагностируются, раннер
сообщает о неустранённой блокировке, и DROP честно завершается ошибкой
ObjectInUse. Проверка границы — blog/tests_runner_teardown.py.

Улики Д68-Т (teardown-утечка): idle-сессия с последним запросом SELECT к
blog_invalidationepoch, живущая до выхода процесса (минимальный
воспроизводитель — квартет классов в blog.tests_epoch; соединение
переоткрывается внутри test_async_asend_resets_cache после
request_started.asend() и ускользает от штатных путей закрытия), и гонка
close→DROP — backend собственного соединения ещё не завершился к моменту
DROP DATABASE.

Подключение: TEST_RUNNER в vsproject/settings/test.py. Конфигурация
проверки области действия: vsproject/settings/test_extra_alias.py.
"""
import os
import time
import uuid

import psycopg2
from django.test.runner import DiscoverRunner

TEST_CLIENT_HOST = "testserver"
DRAIN_TIMEOUT = 5.0


class SiteTestRunner(DiscoverRunner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Метка соединений ЭТОГО запуска; присваивается в setup_databases
        # в OPTIONS всех алиасов ДО открытия каких-либо соединений.
        self._run_app_name = (f"vs-wagtail-tests-{os.getpid()}-"
                              f"{uuid.uuid4().hex[:8]}")

    def setup_databases(self, **kwargs):
        from django.db import connections

        for alias in connections:
            # Ярлык владельца: не подключается ни к чему, только помечает
            # будущие соединения алиаса для границ pg_terminate_backend.
            settings_dict = connections[alias].settings_dict
            options = settings_dict.get("OPTIONS") or {}
            options["application_name"] = self._run_app_name
            settings_dict["OPTIONS"] = options
        old_config = super().setup_databases(**kwargs)
        # К этому моменту DiscoverRunner.setup_databases переключил
        # используемые алиасы на тестовые БД и применил миграции.
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
        """Read-only диагностика сессий на тестовых БД текущего запуска
        (PostgreSQL): и утечки соединений самого процесса, и чужие сессии.
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
           асинхронно, и DROP сразу после close() попадает в гонку
           (явление B: ObjectInUse на собственном умирающем соединении);
        2) дренаж: завершать ТОЛЬКО сессии с меткой ЭТОГО запуска
           (утечка соединения процесса, явление A), дождаться нуля;
        3) чужие сессии (без метки запуска) — диагностировать и сообщить о
           неустранённой блокировке; DROP честно завершится ошибкой.

        Границы: только тестовые БД ЭТОГО запуска (old_config), только
        teardown, только PostgreSQL. Сбой шага не маскирует вердикт сюита —
        исключение печатается, стандартный teardown выполняется как прежде.
        """
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
                report = self._drain_test_db(pg, test_name, self._run_app_name)
                if report["foreign"]:
                    print(f"[test_runner] WARNING: unresolved foreign session(s) "
                          f"on {test_name!r} — раннер завершает только сессии "
                          f"с меткой {self._run_app_name!r}; DROP завершится "
                          f"ошибкой ObjectInUse:")
                    for pid, state, query, backend_start, app in report["foreign"]:
                        snippet = (query or "").strip().replace("\n", " ")[:120]
                        print(f"[test_runner]   foreign pid={pid} state={state} "
                              f"application_name={app!r} backend_start={backend_start} "
                              f"last_query={snippet!r}")
                else:
                    elapsed = round(time.monotonic() - report["deadline"], 1)
                    print(f"[test_runner] test DB {test_name!r} drained in "
                          f"~{elapsed}s (terminated={len(report['terminated'])}); "
                          f"DROP без гонки")
            finally:
                pg.close()

    def _drain_test_db(self, pg, test_name, app_name, except_pids=()):
        """Дренаж одной тестовой БД: завершить ТОЛЬКО сессии с меткой
        app_name (кроме except_pids), дождаться их завершения.

        Возвращает отчёт: terminated (pid'ы завершённых собственных сессий),
        foreign (чужие сессии — НЕ завершаются, ответственность вызывающего),
        deadline (момент начала дренажа для печати длительности).
        """
        deadline = time.monotonic()
        excluded = set(except_pids)
        rows = self._sessions_on(pg, test_name)
        ours = [r for r in rows
                if r[4] == app_name and r[0] not in excluded]
        foreign = [r for r in rows if r[4] != app_name]
        terminated = []
        if ours:
            for pid, state, query, backend_start, app in ours:
                snippet = (query or "").strip().replace("\n", " ")[:120]
                print(f"[test_runner]   terminating leaked own session "
                      f"pid={pid} state={state} backend_start={backend_start} "
                      f"last_query={snippet!r}")
                with pg.cursor() as cur:
                    cur.execute("SELECT pg_terminate_backend(%s)", [pid])
                terminated.append(pid)
            while time.monotonic() - deadline < DRAIN_TIMEOUT:
                rows = self._sessions_on(pg, test_name)
                ours = [r for r in rows
                        if r[4] == app_name and r[0] not in excluded]
                if not ours:
                    break
                time.sleep(0.05)
            if ours:
                pids = [r[0] for r in ours]
                print(f"[test_runner] WARNING: own sessions still present "
                      f"on {test_name!r}: {pids}")
        return {"terminated": terminated, "foreign": foreign,
                "deadline": deadline, "excluded": sorted(excluded)}

    @staticmethod
    def _sessions_on(pg, test_name):
        """Сессии на тестовой БД (опрос из подключения к postgres)."""
        with pg.cursor() as cur:
            cur.execute(
                "SELECT pid, state, query, backend_start, application_name "
                "FROM pg_stat_activity WHERE datname = %s",
                [test_name],
            )
            return cur.fetchall()
