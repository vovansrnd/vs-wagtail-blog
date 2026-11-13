"""Д68-досье: демонстрация пробела отчёта дренажа (найден координатором).

На main@22926cee: если pg_terminate_backend не срабатывает, _drain_test_db
печатает WARNING "own sessions still present", но отчёт НЕ содержит
оставшиеся собственные pid'ы отдельным полем; _drain_and_clear проверяет
только report["foreign"] и печатает "drained ... DROP без гонки" — хотя
DROP впоследствии упадёт с ObjectInUse. Инструмент имитирует отказ terminate
(монкипатч cursor.execute) и показывает фактическую последовательность
сообщений. Исправление — предмет следующего исправляющего PR к раннеру.
"""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vsproject.settings.test")
import django

django.setup()
import psycopg2
from datetime import datetime, timezone
from vsproject.test_runner import SiteTestRunner

DB_NAME = "d68_gap_demo"
RUN_LABEL = "demo-run-label"
creds = dict(user=os.environ.get("DB_USER", "a1020391_django_radio"),
             password=os.environ.get("DB_PASSWORD", "radio-local"),
             host="127.0.0.1", port=5432)
admin = psycopg2.connect(dbname="postgres", **creds)
admin.autocommit = True
with admin.cursor() as cur:
    cur.execute(f"DROP DATABASE IF EXISTS {DB_NAME}")
    cur.execute(f"CREATE DATABASE {DB_NAME}")
own = psycopg2.connect(dbname=DB_NAME, application_name=RUN_LABEL, **creds)
own.autocommit = True
own.cursor().execute("SELECT 1")
own_pid = own.get_backend_pid()
print(f"демо: утёкшая сессия pid={own_pid} на {DB_NAME}")

runner = SiteTestRunner()
pg = psycopg2.connect(dbname="postgres", **creds)
pg.autocommit = True

# Имитация отказа terminate: прокси-подключение, у которого execute
# pg_terminate_backend подменяется на SELECT false (сессия не умирает).
class _CursorProxy:
    def __init__(self, cur):
        self._cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._cur.close()

    def execute(self, sql, args=None):
        if "pg_terminate_backend" in sql:
            print("  [имитация] pg_terminate_backend не сработал — сессия жива")
            self._cur.execute("SELECT false")
            return
        return self._cur.execute(sql, args)

    def fetchall(self, *args, **kwargs):
        return self._cur.fetchall(*args, **kwargs)

class _PgProxy:
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return _CursorProxy(self._real.cursor())

    def __getattr__(self, name):
        return getattr(self._real, name)

proxy = _PgProxy(pg)
try:
    report = runner._drain_test_db(proxy, DB_NAME, RUN_LABEL)
except Exception as exc:  # noqa: BLE001 — как в _drain_and_clear: исключение не маскируется
    print(f"[test_runner] teardown drain failed: {type(exc).__name__}: {exc}")
    report = None

if report is not None:
    print(f"отчёт _drain_test_db: terminated={report['terminated']} "
          f"foreign={[(r[0], r[4]) for r in report['foreign']]} "
          f"— ПОЛЯ собственных выживших pid'ов в отчёте НЕТ")
    # Точная реплика ветки else из _drain_and_clear (main@22926cee):
    print(f"[test_runner] test DB {DB_NAME!r} drained in ~0.0s "
          f"(terminated={len(report['terminated'])}); DROP без гонки")
    print(f"ИТОГ: сообщение 'DROP без гонки' напечатано, "
          f"но сессия {own_pid} всё ещё жива (closed={own.closed}) — "
          f"DROP впоследствии упадёт с ObjectInUse; отчёт не позволил это увидеть")
own.close()
with admin.cursor() as cur:
    cur.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
admin.close()
