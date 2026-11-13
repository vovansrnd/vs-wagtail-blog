"""Д68-досье: трассировка открытия/закрытия соединений с именами потоков.

Запускает заданные тестовые лейблы через SiteTestRunner, ведёт in-memory
журнал NEWCONN/CLOSE (backend_pid -> поток), в конце печатает:
- полный таймлайн тестов и соединений;
- утечки (NEWCONN без CLOSE) с потоком-владельцем.

Использование: trace_connections.py <label1> [label2 ...]
"""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vsproject.settings.test")
import threading

import django

django.setup()
from datetime import datetime, timezone

from django.db.backends.base.base import BaseDatabaseWrapper

TRACE = []
_orig_ensure = BaseDatabaseWrapper.ensure_connection


def traced_ensure(self):
    was = self.connection is not None
    _orig_ensure(self)
    if not was and self.connection is not None:
        t = threading.current_thread()
        TRACE.append(("N", datetime.now(timezone.utc),
                      self.connection.get_backend_pid(), t.name, self.alias))


BaseDatabaseWrapper.ensure_connection = traced_ensure
_close_orig = BaseDatabaseWrapper.close


def traced_close(self):
    had = self.connection is not None
    pid = self.connection.get_backend_pid() if had else None
    _close_orig(self)
    if had and pid is not None:
        t = threading.current_thread()
        TRACE.append(("C", datetime.now(timezone.utc), pid, t.name, self.alias))


BaseDatabaseWrapper.close = traced_close

import unittest


class TimedResult(unittest.TextTestResult):
    def _ts(self, test, phase):
        TRACE.append(("T", datetime.now(timezone.utc), phase, str(test), ""))

    def startTest(self, test):
        self._ts(test, "START")
        super().startTest(test)

    def addSuccess(self, test):
        self._ts(test, "END-ok")
        super().addSuccess(test)

    def addError(self, test, err):
        self._ts(test, "END-ERROR")
        super().addError(test, err)

    def addFailure(self, test, err):
        self._ts(test, "END-FAIL")
        super().addFailure(test, err)


from vsproject.test_runner import SiteTestRunner


class TimedRunner(SiteTestRunner):
    def get_test_runner_kwargs(self):
        kwargs = super().get_test_runner_kwargs()
        kwargs["resultclass"] = TimedResult
        return kwargs


labels = sys.argv[1:] or ["blog.tests_epoch"]
runner = TimedRunner(verbosity=0, interactive=False)
runner.run_tests(labels)

opened, closed = {}, {}
for kind, ts, a, b, c in TRACE:
    if kind == "N":
        opened[a] = (ts, b)
    elif kind == "C":
        closed[a] = (ts, b)
leaks = [p for p in opened if p not in closed]
print(f"=== TIMELINE (тесты и соединения) ===")
for kind, ts, a, b, c in TRACE:
    if kind == "T":
        print(f"{ts:%H:%M:%S.%f} TEST {b} {c[:90]}")
    else:
        mark = "LEAK" if (kind == "N" and a in leaks) else kind
        print(f"{ts:%H:%M:%S.%f} {mark:4} pid={a} thread={b!r} alias={c}")
print(f"=== УТЕЧКИ (NEWCONN без CLOSE): {[(p, opened[p][1]) for p in leaks]} ===")
