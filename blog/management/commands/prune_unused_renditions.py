"""
Удаляет рендишены изображений, фильтры которых не найдены в шаблонах,
настройках Wagtail, текущих Page body и сохранённых Wagtail revisions.

Оригиналы не удаляются. Команда работает в fail-closed режиме: если сканер
содержимого не может завершить проверку, --apply не выполняется.

По умолчанию dry-run (только отчёт). Для реального удаления: --apply.

Дополнительные спеки можно сохранить через settings.RENDITION_EXTRA_WHITELIST
(список строк), например заданные динамически во view.
"""
import os
import re

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from wagtail_image_directories.references import (
    build_image_file_target_index,
    scan_content_and_revision_references,
)

# Спеки, которые генерирует сама админка Wagtail (миниатюры списков,
# выбора изображения и т.п.). Их удалять нельзя — иначе админка будет
# бесконечно пересоздавать их.
ADMIN_SPECS = {
    "max-165x165", "fill-5x5", "fill-68x68", "fill-80x80", "fill-100x100",
    "fill-100x150", "fill-150x100", "fill-200x200", "fill-400x400",
    "fill-640x480", "fill-800x600", "fill-1000x800", "min-120x120",
    "width-32", "max-800x600", "max-320x320",
}

# Токен спеки wagtail: действие-числа(-числа)*, например fill-800x450, width-600
SPEC_TOKEN_RE = re.compile(
    r"\b(?:fill|max|min|width|height|scale|jpeg|webp|avif|original)"
    r"(?:-[0-9]+(?:x[0-9]+)*)+\b"
)

TEMPLATE_EXTS = (".html", ".txt")


def collect_template_dirs():
    dirs = set()
    # DIRS из TEMPLATES
    for cfg in getattr(settings, "TEMPLATES", []):
        dirs.update(cfg.get("DIRS") or [])
    # templates/ каждого установленного приложения (включая плагины)
    for app in apps.get_app_configs():
        path = os.path.join(app.path, "templates")
        if os.path.isdir(path):
            dirs.add(path)
    return {d for d in dirs if os.path.isdir(d)}


def specs_from_templates():
    found = set()
    for base in collect_template_dirs():
        for root, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith(TEMPLATE_EXTS):
                    continue
                try:
                    with open(os.path.join(root, name), encoding="utf-8", errors="ignore") as fh:
                        found.update(SPEC_TOKEN_RE.findall(fh.read()))
                except OSError:
                    continue
    return found


class Command(BaseCommand):
    help = (
        "Удаляет неиспользуемые рендишены изображений. "
        "По умолчанию dry-run, реальное удаление — с --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Реально удалять (по умолчанию только отчёт)",
        )

    def handle(self, *args, **options):
        rendition_model = apps.get_model(
            settings.WAGTAILIMAGES_RENDITION_MODEL
        )

        whitelist = ADMIN_SPECS | specs_from_templates()
        # Рендишен, который prose editor вставляет по умолчанию (настройка
        # задаётся вне шаблонов, поэтому скан их не видит)
        whitelist |= {
            getattr(settings, "WAGTAIL_PROSE_EDITOR_IMAGES_RENDITION_SPEC", "width-1200")
        }
        whitelist |= set(getattr(settings, "RENDITION_EXTRA_WHITELIST", []))

        # Direct Prose Editor URLs are hard-coded in current page bodies and
        # historical Wagtail revisions. Keep every filter spec whose exact
        # rendition URL is referenced by either source. If scanning fails, fail
        # closed: never offer deletion based on an incomplete inventory.
        try:
            rendition_targets = build_image_file_target_index(
                include_originals=False,
                include_renditions=True,
            )
            reference_scan = scan_content_and_revision_references(rendition_targets)
        except Exception as exc:
            raise CommandError(
                "Не удалось полностью проверить ссылки в body и Wagtail revisions; "
                "удаление рендишенов отменено."
            ) from exc

        referenced_specs = set(reference_scan["referenced_rendition_specs"])
        whitelist |= referenced_specs
        self.stdout.write(f"Whitelist: {len(whitelist)} спеков")
        self.stdout.write(
            "Ссылки из контента: "
            f"{len(reference_scan['matches'])}; "
            f"контентных значений: {reference_scan['current_content_values_scanned']}; "
            f"revisions: {reference_scan['revision_records_scanned']}; "
            f"защищённых спеков: {len(referenced_specs)}"
        )
        for spec in sorted(referenced_specs):
            self.stdout.write(f"  KEEP (ссылка в body/revision): {spec}")

        # Статистика по существующим рендишенам
        by_spec = {}
        for row in rendition_model.objects.values_list("filter_spec"):
            by_spec[row[0]] = by_spec.get(row[0], 0) + 1

        keep, prune = [], []
        for spec, count in sorted(by_spec.items(), key=lambda kv: -kv[1]):
            if spec in whitelist:
                keep.append((spec, count))
            else:
                prune.append((spec, count))

        self.stdout.write("")
        total_prune = sum(c for _s, c in prune)
        self.stdout.write(f"Всего рендишенов: {sum(by_spec.values())}")
        self.stdout.write(f"Оставляем: {sum(c for _s, c in keep)} (спеков: {len(keep)})")
        self.stdout.write(f"К удалению: {total_prune} (спеков: {len(prune)})")
        for spec, count in prune:
            self.stdout.write(f"  DELETE {spec}: {count}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry-run. Для удаления: --apply"))
            return

        deleted_files = 0
        for spec, _count in prune:
            qs = rendition_model.objects.filter(filter_spec=spec)
            for rend in qs.iterator(chunk_size=500):
                try:
                    rend.file.delete(save=False)
                except Exception:
                    pass  # файла может уже не быть — строку всё равно удаляем
                rend.delete()
                deleted_files += 1
            self.stdout.write(f"  удалено {spec}")

        self.stdout.write(
            self.style.SUCCESS(f"Готово: удалено {deleted_files} рендишенов")
        )
