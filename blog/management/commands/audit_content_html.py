"""Read-only diff audit: show what the current sanitizer would change.

This command never saves pages. It reports changed page/field counts and a
before/after inventory of HTML elements and attributes, rather than relying on
substring matches such as '<script' alone.
"""
from collections import Counter
from html.parser import HTMLParser

from django.core.management.base import BaseCommand

from blog.html_sanitizer import sanitize_html
from blog.models import BlogPage


class _MarkupInventory(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.counts = Counter()

    def handle_starttag(self, tag, attrs):
        self.counts[f"tag:{tag}"] += 1
        for name, _value in attrs:
            self.counts[f"attr:{tag}@{name}"] += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def _inventory(value):
    parser = _MarkupInventory()
    parser.feed(value or "")
    parser.close()
    return parser.counts


class Command(BaseCommand):
    help = "Показывает diff санитайзера по body/excerpt; БД не изменяет."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="Максимум примеров страниц")
        parser.add_argument("--all", action="store_true", help="Показать все изменённые страницы")

    def handle(self, *args, **options):
        limit = max(0, options["limit"])
        before = Counter()
        after = Counter()
        changed_pages = 0
        changed_fields = 0
        samples = []

        qs = BlogPage.objects.only("id", "title", "body", "excerpt").order_by("pk")
        total = qs.count()
        for page in qs.iterator(chunk_size=250):
            page_changed = False
            for field in ("body", "excerpt"):
                original = str(getattr(page, field) or "")
                cleaned = sanitize_html(original)
                before.update(_inventory(original))
                after.update(_inventory(cleaned))
                if cleaned != original:
                    page_changed = True
                    changed_fields += 1
            if page_changed:
                changed_pages += 1
                if options["all"] or len(samples) < limit:
                    samples.append((page.pk, page.title[:80]))

        self.stdout.write(f"Страниц проверено: {total}")
        self.stdout.write(f"Страниц с изменениями санитайзера: {changed_pages}")
        self.stdout.write(f"Полей body/excerpt с изменениями: {changed_fields}")
        self.stdout.write("Изменения по элементам/атрибутам (до → после):")
        for key in sorted(set(before) | set(after)):
            old_count, new_count = before[key], after[key]
            if old_count != new_count:
                self.stdout.write(f"  {key}: {old_count} → {new_count}")
        self.stdout.write("Примеры страниц:")
        for pk, title in samples:
            self.stdout.write(f"  id={pk} | {title}")
        if not options["all"] and changed_pages > len(samples):
            self.stdout.write(f"  … ещё {changed_pages - len(samples)}; используйте --all")
        self.stdout.write(self.style.SUCCESS("Read-only diff завершён; содержимое не изменялось."))
