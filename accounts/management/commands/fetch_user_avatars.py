from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.services import fetch_avatar_for_user


class Command(BaseCommand):
    help = "Скачивает локальные аватары из socialaccount; Gravatar включается настройкой AVATAR_FETCH_GRAVATAR"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            help="Обработать только одного пользователя по username",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Перекачать даже если уже есть локальный аватар или уже была проверка",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        qs = User.objects.all().order_by("id")

        username = options.get("username")
        if username:
            qs = qs.filter(username=username)

        force = options.get("force", False)

        total = 0
        downloaded = 0
        skipped = 0
        not_found = 0

        for user in qs:
            total += 1
            result = fetch_avatar_for_user(user, force=force)

            status = result.get("status")

            if status == "downloaded":
                downloaded += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[OK] {user} -> {result.get('source')}"
                    )
                )

            elif status == "skipped":
                skipped += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[SKIP] {user} -> {result.get('reason')}"
                    )
                )

            elif status == "not_found":
                not_found += 1
                self.stdout.write(
                    f"[NONE] {user} -> avatar not found"
                )

            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"[ERR] {user} -> unknown result: {result}"
                    )
                )

        self.stdout.write("")
        self.stdout.write(f"Всего: {total}")
        self.stdout.write(self.style.SUCCESS(f"Скачано: {downloaded}"))
        self.stdout.write(self.style.WARNING(f"Пропущено: {skipped}"))
        self.stdout.write(f"Не найдено: {not_found}")
