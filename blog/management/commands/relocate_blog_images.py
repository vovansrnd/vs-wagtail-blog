"""Read-only planner for legacy image-directory relocation.

The old command attempted writes by default, queried a non-existent ``directory``
field, and could move images shared by pages. A safe move cannot be inferred from
live BlogPage.body alone: draft revisions, other page types, snippets, and custom
relations may also refer to an image. This command therefore only reports
candidates. It deliberately has no write/apply option until a complete reference
scanner and an owner-approved migration procedure exist.
"""
import re
from collections import defaultdict

from django.core.exceptions import FieldDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify
from wagtail.images import get_image_model

from blog.models import BlogPage

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
IMG_ID_RE = re.compile(
    r"(?<![\w:-])(?:data-id|id)\s*=\s*([\"'])(\d+)\1", re.IGNORECASE
)
Image = get_image_model()


def image_ids_from_body(body):
    """Return explicit Wagtail image IDs in img tags (not arbitrary numeric IDs)."""
    result = set()
    for tag in IMG_TAG_RE.findall(str(body or "")):
        result.update(int(match[1]) for match in IMG_ID_RE.findall(tag))
    return result


def _page_destination(page):
    published = getattr(page, "date_published", None) or getattr(
        page, "first_published_at", None
    )
    if not published:
        return None
    slug = slugify(page.slug, allow_unicode=True)
    if not slug:
        return None
    return str(published.year), slug


class Command(BaseCommand):
    help = (
        "Только формирует план переноса legacy images; файлы и БД не изменяет. "
        "Автоматический --apply намеренно отсутствует."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Ограничить число кандидатов в выводе (0 = без лимита)",
        )

    def handle(self, *args, **options):
        try:
            Image._meta.get_field("year")
            Image._meta.get_field("slug")
            Image._meta.get_field("file")
        except FieldDoesNotExist as exc:
            raise CommandError(
                "Image model lacks year/slug/file fields; no plan can be generated."
            ) from exc

        destinations = defaultdict(set)
        # Include drafts too. This is still not a complete reference scan, hence
        # this command cannot perform moves and labels its output as candidates.
        pages = BlogPage.objects.all().only(
            "pk", "slug", "date_published", "first_published_at", "body", "main_image_id"
        ).iterator(chunk_size=250)
        for page in pages:
            destination = _page_destination(page)
            if destination is None:
                continue
            ids = image_ids_from_body(page.body)
            if page.main_image_id:
                ids.add(page.main_image_id)
            for image_id in ids:
                destinations[image_id].add(destination)

        candidate_count = 0
        ambiguous_count = 0
        already_directory_count = 0
        missing_image_count = 0
        images = Image.objects.filter(pk__in=destinations.keys()).order_by("pk")
        limit = max(0, options["limit"])
        printed = 0

        for image in images.iterator(chunk_size=250):
            targets = destinations[image.pk]
            if len(targets) != 1:
                ambiguous_count += 1
                self.stdout.write(
                    f"SKIP-CANDIDATE image={image.pk}: {len(targets)} destination directories"
                )
                continue
            if getattr(image, "year", "misc") not in ("", "misc") or getattr(image, "slug", ""):
                already_directory_count += 1
                continue
            if not image.file or not image.file.name:
                missing_image_count += 1
                continue

            year, slug = next(iter(targets))
            candidate_count += 1
            if not limit or printed < limit:
                self.stdout.write(
                    f"CANDIDATE image={image.pk}: {image.file.name} -> year={year}, slug={slug}"
                )
                printed += 1

        self.stdout.write(self.style.WARNING(
            "READ-ONLY PLAN ONLY: no files, image rows, renditions, or page revisions were changed."
        ))
        self.stdout.write(
            f"candidates={candidate_count}, ambiguous={ambiguous_count}, "
            f"already_directory={already_directory_count}, missing_file={missing_image_count}"
        )
        self.stdout.write(
            "No automatic apply mode exists: a complete scan of revisions and all image relations is required first."
        )
