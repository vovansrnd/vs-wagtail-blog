"""Rewrite legacy Zenway image URLs in live BlogPage bodies.

Read-only dry-run is the default. --apply writes each changed page as a new
Wagtail revision and publishes it in its own transaction.
"""
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from blog.models import BlogPage

LEGACY_IMAGE_RE = re.compile(
    r"(?P<attr>src|href)=(?P<quote>[\"'])"
    r"(?P<url>(?:https?://radio\.zenway\.ru)?/uploads/_pages/\d+/[^\"']+\.(?:jpg|jpeg|png|gif))"
    r"(?P=quote)",
    re.IGNORECASE,
)


def rewrite_legacy_image_urls(body):
    media_prefix = settings.MEDIA_URL.rstrip("/")
    count = 0

    def replace(match):
        nonlocal count
        raw = match.group("url")
        try:
            path = urlsplit(raw).path
        except ValueError:
            return match.group(0)
        if not path.startswith("/uploads/_pages/"):
            return match.group(0)
        count += 1
        return f"{match.group('attr')}={match.group('quote')}{media_prefix}{path}{match.group('quote')}"

    return LEGACY_IMAGE_RE.sub(replace, str(body or "")), count


class Command(BaseCommand):
    help = "Исправляет старые URL изображений Zenway; по умолчанию только отчёт."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Создать и опубликовать новые Wagtail revisions")
        parser.add_argument("--limit", type=int, default=0, help="Ограничить число страниц (0 = без лимита)")

    def handle(self, *args, **options):
        qs = BlogPage.objects.live().order_by("pk")
        if options["limit"]:
            qs = qs[:max(0, options["limit"])]
        examined = changed = replacements = 0

        for page in qs.iterator(chunk_size=100):
            examined += 1
            updated, count = rewrite_legacy_image_urls(page.body)
            if not count or updated == str(page.body or ""):
                continue
            changed += 1
            replacements += count
            self.stdout.write(f"{'APPLY' if options['apply'] else 'DRY'} id={page.pk} {page.title[:70]}: {count} URL")
            if options["apply"]:
                with transaction.atomic():
                    page.body = updated
                    revision = page.save_revision()
                    revision.publish()

        self.stdout.write(
            self.style.SUCCESS(
                f"Завершено: проверено={examined}, страниц={changed}, замен={replacements}, "
                f"режим={'apply' if options['apply'] else 'dry-run'}"
            )
        )
