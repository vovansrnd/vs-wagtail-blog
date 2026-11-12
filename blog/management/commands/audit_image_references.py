"""Read-only inventory of Wagtail image URLs used in page bodies/revisions.

The command does not access the media file contents and never changes the
filesystem or database. It is intended to run before image relocation,
delete, or rendition pruning is considered.
"""
import json
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from wagtail.images import get_image_model

from wagtail_image_directories.references import (
    build_image_file_target_index,
    find_live_image_relation_references,
    find_revision_image_relation_references,
    scan_content_and_revision_references,
)


class Command(BaseCommand):
    help = "Read-only audit of image/rendition URLs referenced by Page content, rich-text model fields, and revisions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            help="Optional path for a JSON report. The command never writes to the database or media storage.",
        )
        parser.add_argument(
            "--sample",
            type=int,
            default=50,
            help="Maximum number of reference examples printed to the console (default: 50).",
        )

    def handle(self, *args, **options):
        Image = get_image_model()
        try:
            target_index = build_image_file_target_index(
                include_originals=True,
                include_renditions=True,
            )
            content_scan = scan_content_and_revision_references(target_index)
            image_ids = list(Image.objects.values_list("pk", flat=True).iterator(chunk_size=1000))
            relation_refs = (
                find_live_image_relation_references(image_ids)
                + find_revision_image_relation_references(image_ids)
            )
        except Exception as exc:
            raise CommandError(
                "Аудит ссылок не завершён. Результат неполный; никаких выводов о безопасности "
                "удаления/перемещения делать нельзя."
            ) from exc

        by_image = defaultdict(list)
        for row in content_scan["matches"]:
            target = row["target"]
            image_id = target.get("image_id")
            if image_id is None:
                continue
            by_image[int(image_id)].append({
                "kind": row["source"]["kind"],
                "source_model": row["source"].get("model"),
                "source_object_id": row["source"].get("object_id"),
                "revision_id": row["source"].get("revision_id"),
                "field": row["source"].get("field"),
                "file_kind": target.get("kind"),
                "rendition_id": target.get("rendition_id"),
                "filter_spec": target.get("filter_spec"),
                "path": row["path"],
            })
        for row in relation_refs:
            by_image[int(row["image_id"])].append(row)

        image_qs = Image.objects.filter(pk__in=by_image).only("pk", "title", "file")
        image_meta = {
            image.pk: {
                "title": image.title,
                "file_name": image.file.name if image.file else "",
            }
            for image in image_qs.iterator(chunk_size=500)
        }
        report = {
            "read_only": True,
            "image_count": Image.objects.count(),
            "url_paths_indexed": len(target_index),
            "current_content_values_scanned": content_scan["current_content_values_scanned"],
            "revision_records_scanned": content_scan["revision_records_scanned"],
            "content_url_matches": len(content_scan["matches"]),
            "relation_matches": len(relation_refs),
            "images_with_references": len(by_image),
            "referenced_images": [
                {
                    "image_id": image_id,
                    **image_meta.get(image_id, {}),
                    "references": refs,
                }
                for image_id, refs in sorted(by_image.items())
            ],
            "limitations": [
                "Scans all current text/JSON fields on Wagtail Page subclasses and common content fields (body/content/text/message/comment/response/etc.) on other installed models, plus all Wagtail revision text leaves.",
                "Scans concrete model FK/O2O/M2M relations to the configured image model.",
                "Does not discover external backlinks, browser caches, CDN copies, arbitrary CSS/JS outside scanned content fields, or references in non-revision models whose field names are not in the scanner's content-field allowlist.",
                "A clean result is not proof that deleting or moving an image is globally safe.",
            ],
        }

        self.stdout.write(self.style.SUCCESS("READ-ONLY image reference audit completed"))
        for key in (
            "image_count", "url_paths_indexed", "current_content_values_scanned",
            "revision_records_scanned", "content_url_matches",
            "relation_matches", "images_with_references",
        ):
            self.stdout.write(f"{key}: {report[key]}")

        sample = max(0, options["sample"])
        for row in report["referenced_images"][:sample]:
            self.stdout.write(
                f"IMAGE {row['image_id']} {row.get('file_name', '')}: "
                f"{len(row['references'])} reference(s)"
            )
            for ref in row["references"][:5]:
                source = ref.get("source_model") or ref.get("model") or ref.get("kind")
                object_id = ref.get("source_object_id", ref.get("object_id", ""))
                path = ref.get("path", "")
                self.stdout.write(f"  - {source}#{object_id} {path}")

        if options["output"]:
            try:
                with open(options["output"], "w", encoding="utf-8") as fh:
                    json.dump(report, fh, ensure_ascii=False, indent=2)
            except OSError as exc:
                raise CommandError(f"Не удалось записать JSON-отчёт: {exc}") from exc
            self.stdout.write(f"JSON report: {options['output']}")

        self.stdout.write(self.style.WARNING(
            "This report is advisory. Do not relocate or delete images solely because they have no reported reference."
        ))
