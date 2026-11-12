"""Удаление просроченных записей UsedFormToken (старше CONTACT_FORM_TOKEN_MAX_AGE).

Запуск по крону или вручную. Таблица маленькая — удаление быстрое.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from blog.models import UsedFormToken


class Command(BaseCommand):
    help = "Удаляет просроченные записи UsedFormToken (старше CONTACT_FORM_TOKEN_MAX_AGE)"

    def handle(self, *args, **options):
        from django.conf import settings
        max_age = int(getattr(settings, "CONTACT_FORM_TOKEN_MAX_AGE", 24 * 60 * 60))
        cutoff = timezone.now() - timedelta(seconds=max_age)
        deleted, _ = UsedFormToken.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(f"Удалено просроченных токенов: {deleted}")
