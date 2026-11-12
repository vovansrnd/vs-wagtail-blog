"""Normalize malformed paragraph/image/hr wrappers in live BlogPage HTML.

Dry-run is the default. --apply creates and publishes a Wagtail revision for
_each_ changed page inside its own transaction; no direct body-only UPDATE is
performed, so the published revision and page row stay in sync.
"""
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db import transaction

from blog.models import BlogPage


def clean_body(original_body):
    soup = BeautifulSoup(str(original_body or ""), "html.parser")

    for hr_tag in list(soup.find_all("hr")):
        parent_p = hr_tag.find_parent("p")
        if parent_p:
            parent_p.insert_after(hr_tag)
            if not parent_p.get_text(strip=True) and not parent_p.find_all():
                parent_p.extract()

    for p_tag in list(soup.find_all("p")):
        content = p_tag.get_text(strip=True)
        images = p_tag.find_all("img", recursive=False)
        other_elements = [child for child in p_tag.contents if getattr(child, "name", None) != "img"]

        if not content and len(images) == 1 and not other_elements:
            p_tag.insert_after(images[0])
            p_tag.extract()
        elif not content and not p_tag.find_all(lambda tag: tag.name not in {"br"}):
            p_tag.extract()

    return str(soup)


class Command(BaseCommand):
    help = "Нормализует HTML body BlogPage; по умолчанию только отчёт, --apply публикует revision."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Создать и опубликовать новую Wagtail revision")
        parser.add_argument("--limit", type=int, default=0, help="Ограничить число страниц (0 = без лимита)")

    def handle(self, *args, **options):
        qs = BlogPage.objects.live().order_by("pk")
        if options["limit"]:
            qs = qs[:max(0, options["limit"])]
        examined = changed = 0

        for page in qs.iterator(chunk_size=100):
            examined += 1
            updated = clean_body(page.body)
            if updated == str(page.body or ""):
                continue
            changed += 1
            self.stdout.write(f"{'APPLY' if options['apply'] else 'DRY'} id={page.pk} {page.title[:70]}")
            if options["apply"]:
                with transaction.atomic():
                    page.body = updated
                    revision = page.save_revision()
                    revision.publish()

        self.stdout.write(
            self.style.SUCCESS(
                f"Завершено: проверено={examined}, с изменениями={changed}, "
                f"режим={'apply' if options['apply'] else 'dry-run'}"
            )
        )
