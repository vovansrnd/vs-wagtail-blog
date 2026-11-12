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
