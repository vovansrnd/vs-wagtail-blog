"""Д68-Т: проверка границы безопасности дренажа тестовых БД.

pg_terminate_backend в дренаже раннера (vsproject/test_runner.py) вправе
завершать только сессии ЭТОГО запуска тестов: раннер помечает соединения
application_name с уникальной меткой запуска (pid + случайный суффикс).
Чужая сессия на той же тестовой БД (параллельный запуск тестов,
диагностика разработчика) НЕ завершается — дренаж сообщает о ней как о
неустранённой блокировке, и DROP честно падает с ObjectInUse.

Проверка на живой тестовой БД:
- собственная «утёкшая» сессия (метка запуска) — завершается дренажем;
- чужая сессия (другая application_name) — переживает дренаж;
- живая сессия самого TestCase (исключённая по pid) — не затронута.

Тест PostgreSQL-only (границы определены только для PostgreSQL).
"""
import unittest

import psycopg2
from django.conf import settings
from django.db import connection
from django.test import TestCase

from vsproject.test_runner import SiteTestRunner


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL-only")
class RunnerDrainScopingTests(TestCase):
    def test_drain_terminates_own_leak_and_spares_foreign_session(self):
        run_label = (connection.settings_dict.get("OPTIONS") or {}).get(
            "application_name")
        self.assertIsNotNone(
            run_label, "раннер обязан пометить сессии запуска application_name")
        db = settings.DATABASES["default"]
        creds = dict(
            user=db["USER"], password=db["PASSWORD"],
            host=db["HOST"] or None, port=db["PORT"] or None,
        )
        test_name = connection.settings_dict["NAME"]

        def alive(conn):
            """SELECT 1 по соединению; False, если backend завершён."""
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                return True
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                return False

        # «утёкшая» сессия ЭТОГО запуска (метка совпадает с запуском)
        own = psycopg2.connect(dbname=test_name, application_name=run_label,
                               **creds)
        own.autocommit = True
        own.cursor().execute("SELECT 1")
        own_pid = own.get_backend_pid()
        # чужая сессия (метка другая — имитация параллельного запуска)
        foreign = psycopg2.connect(dbname=test_name,
                                   application_name="d68-foreign-diagnostic",
                                   **creds)
        foreign.autocommit = True
        foreign.cursor().execute("SELECT 1")
        foreign_pid = foreign.get_backend_pid()
        # живая сессия TestCase (несёт метку запуска) — исключается по pid
        wrapper_pid = connection.connection.get_backend_pid()

        pg = psycopg2.connect(dbname="postgres", **creds)
        pg.autocommit = True
        runner = SiteTestRunner()
        try:
            report = runner._drain_test_db(
                pg, test_name, run_label, except_pids=[wrapper_pid])
            with pg.cursor() as cur:
                cur.execute("SELECT pid, application_name FROM pg_stat_activity "
                            "WHERE datname = %s", [test_name])
                server_state = cur.fetchall()

            # собственная утечка завершена дренажем (и по факту мертва)
            self.assertIn(own_pid, report["terminated"],
                          f"дренаж должен завершать собственную утечку: {report}")
            self.assertFalse(alive(own),
                             "собственная утечка должна быть завершена")
            # чужая сессия НЕ завершена и ПЕРЕЖИЛА дренаж (и на сервере)
            self.assertIn(foreign_pid, [row[0] for row in report["foreign"]],
                          "чужая сессия должна попасть в неустранённую "
                          "блокировку")
            self.assertNotIn(foreign_pid, report["terminated"],
                             "чужая сессия не должна завершаться дренажем")
            self.assertIn(
                (foreign_pid, "d68-foreign-diagnostic"),
                [(pid, app) for pid, app in server_state],
                "чужая сессия должна остаться на сервере после дренажа")
            self.assertTrue(alive(foreign), "чужая сессия должна пережить дренаж")
            # живая сессия TestCase (исключённая) цела
            self.assertNotIn(wrapper_pid, report["terminated"])
            self.assertTrue(alive(connection.connection),
                            "сессия TestCase не должна затрагиваться дренажем")
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            # закрытия — только ПОСЛЕ всех проверок (это сценарий теста,
            # а не дренажа)
            foreign.close()
            pg.close()
        # после явного клиентского закрытия объект считывается как закрытый
        self.assertFalse(alive(foreign))
