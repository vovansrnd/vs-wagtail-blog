import os
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from accounts.models import UserProfile
from wagtail_simple_forum.models import ForumLegacyUserMap


class Command(BaseCommand):
    help = "Импортирует аватары из старого форума по legacy_id"

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=str,
            default="img/avatars",
            help="Путь к папке со старыми аватарами (по умолчанию img/avatars)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Перезаписать аватар, даже если уже есть",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет сделано, без записи",
        )

    def handle(self, *args, **options):
        source_dir = Path(options["source"])
        force = options["force"]
        dry_run = options["dry_run"]

        if not source_dir.exists():
            self.stderr.write(self.style.ERROR(
                f"Папка не найдена: {source_dir.resolve()}"
            ))
            return

        if not source_dir.is_dir():
            self.stderr.write(self.style.ERROR(
                f"Указанный путь — не папка: {source_dir.resolve()}"
            ))
            return

        # Собираем все файлы, где имя (без расширения) — число
        avatar_files = {}
        for f in source_dir.iterdir():
            if not f.is_file():
                continue

            stem = f.stem  # имя без расширения
            suffix = f.suffix.lower()

            if suffix not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                self.stdout.write(
                    self.style.WARNING(f"[SKIP] {f.name} — неподдерживаемый формат")
                )
                continue

            try:
                legacy_id = int(stem)
            except ValueError:
                self.stdout.write(
                    self.style.WARNING(f"[SKIP] {f.name} — имя не является числом")
                )
                continue

            # Пропускаем Guest (legacy_id=1)
            if legacy_id == 1:
                self.stdout.write(
                    self.style.WARNING(f"[SKIP] {f.name} — Guest (id=1)")
                )
                continue

            avatar_files[legacy_id] = f

        if not avatar_files:
            self.stdout.write("Подходящих файлов не найдено.")
            return

        self.stdout.write(f"Найдено файлов аватаров: {len(avatar_files)}")
        self.stdout.write("")

        # Загружаем все маппинги разом
        legacy_maps = {
            m.legacy_id: m
            for m in ForumLegacyUserMap.objects.select_related("user").all()
        }

        total = 0
        imported = 0
        skipped_exists = 0
        skipped_no_map = 0
        errors = 0

        for legacy_id, filepath in sorted(avatar_files.items()):
            total += 1

            # Ищем маппинг
            legacy_map = legacy_maps.get(legacy_id)
            if not legacy_map:
                self.stdout.write(
                    f"[NO MAP] {filepath.name} — legacy_id={legacy_id} не найден в ForumLegacyUserMap"
                )
                skipped_no_map += 1
                continue

            user = legacy_map.user
            username = legacy_map.legacy_username or user.username

            # Получаем или создаём профиль
            profile, created = UserProfile.objects.get_or_create(user=user)

            if created:
                self.stdout.write(
                    self.style.NOTICE(
                        f"  [NEW PROFILE] Создан профиль для {username} (user.id={user.id})"
                    )
                )

            # Проверяем, есть ли уже аватар
            if profile.avatar and not force:
                self.stdout.write(
                    self.style.WARNING(
                        f"[EXISTS] {filepath.name} → {username} (user.id={user.id}) — аватар уже есть, пропускаю"
                    )
                )
                skipped_exists += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[DRY] {filepath.name} → {username} (user.id={user.id}) — будет импортирован"
                    )
                )
                imported += 1
                continue

            # Читаем и сохраняем
            try:
                ext = filepath.suffix.lower()
                if ext == ".jpeg":
                    ext = ".jpg"

                content = filepath.read_bytes()
                filename = f"avatar{ext}"

                # Удаляем старый файл, если перезаписываем
                if profile.avatar and force:
                    profile.avatar.delete(save=False)

                profile.avatar.save(filename, ContentFile(content), save=False)
                profile.avatar_source = "legacy_forum"
                profile.save(update_fields=["avatar", "avatar_source", "updated_at"])

                self.stdout.write(
                    self.style.SUCCESS(
                        f"[OK] {filepath.name} → {username} (user.id={user.id})"
                    )
                )
                imported += 1

            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(
                        f"[ERR] {filepath.name} → {username}: {e}"
                    )
                )
                errors += 1

        # Итог
        self.stdout.write("")
        self.stdout.write(f"Всего файлов: {total}")
        self.stdout.write(self.style.SUCCESS(f"Импортировано: {imported}"))
        self.stdout.write(self.style.WARNING(f"Пропущено (уже есть): {skipped_exists}"))
        self.stdout.write(f"Пропущено (нет маппинга): {skipped_no_map}")

        if errors:
            self.stdout.write(self.style.ERROR(f"Ошибок: {errors}"))

        if dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.NOTICE("Это был dry-run. Ничего не записано."))
