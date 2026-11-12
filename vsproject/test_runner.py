"""Тестовый раннер Д68: запись Django Site для Host тестового клиента.

CurrentSiteMiddleware (django.contrib.sites) резолвит сайт по Host запроса.
Django-тестовый клиент ходит с Host "testserver"; в чистой установке среди
записей django.contrib.sites.models.Site его нет, а SITE_ID глобально
сознательно не задаётся: многосайтовый проект выбирает сайт по Host, и
фиксированная запись в общих настройках подменяла бы этот выбор для всех
доменов. Поэтому запись создаёт ТЕСТОВЫЙ раннер в каждой тестовой БД после
миграций. Это инфраструктура тестов: dev/production настройки,
wagtail.models.Site и маршрутизация трёх доменов не затрагиваются.

Подключение: TEST_RUNNER в vsproject/settings/test.py.
"""
from django.test.runner import DiscoverRunner

TEST_CLIENT_HOST = "testserver"


class SiteTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        # К этому моменту DiscoverRunner.setup_databases переключил все
        # настроенные алиасы на тестовые БД и применил миграции.
        from django.contrib.sites.models import Site
        from django.db import connections

        for alias in connections:
            Site.objects.using(alias).get_or_create(
                domain=TEST_CLIENT_HOST,
                defaults={"name": f"{TEST_CLIENT_HOST} (Django test client)"},
            )
        return old_config

    def teardown_databases(self, old_config):
        try:
            self._terminate_foreign_sessions()
        except Exception as exc:  # noqa: BLE001 — teardown не должен маскировать вердикт
            print(f"[test_runner] teardown diagnostics failed: "
                  f"{type(exc).__name__}: {exc}")
        return super().teardown_databases(old_config)

    def _terminate_foreign_sessions(self):
        """Завершить ПОСТОРОННИЕ сессии тестовых БД перед DROP.

        Сторонняя сессия (соединение из потока теста, не закрытое
        connection.close()) ломает DROP DATABASE с ObjectInUse: вердикт
        сюита зелёный, а exit code всего процесса — 1. Тестовая БД —
        расходный артефакт прогона, завершение посторонних сессий в ней
        безопасно и не касается production. Диагностика печатает PID,
        состояние и последний запрос каждой такой сессии — это и есть
        фиксация причины утечки соединений.
        """
        from django.db import connections

        for alias in connections:
            connection = connections[alias]
            if connection.vendor != "postgresql":
                continue
            test_db = connection.settings_dict.get("NAME")
            if not test_db:
                continue
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pid, state, query FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    [test_db],
                )
                stragglers = cursor.fetchall()
            if not stragglers:
                continue
            for pid, state, query in stragglers:
                snippet = (query or "").strip().replace("\n", " ")[:120]
                print(f"[test_runner] straggler session on {test_db}: "
                      f"pid={pid} state={state} last_query={snippet!r}")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    [test_db],
                )
