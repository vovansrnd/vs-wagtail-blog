import os

from django.core.management.base import BaseCommand
from blog.models import CustomImage
from wagtail.models import Collection


class Command(BaseCommand):
    help = 'Sync image year/slug from file paths'

    def handle(self, *args, **options):
        images = CustomImage.objects.all()
        updated = 0
        errors = 0

        self.stdout.write(f"Обработка {images.count()} изображений...")

        for image in images:
            if not image.file or not image.file.name:
                continue

            # Разбираем путь файла
            parts = image.file.name.split('/')

            # Ожидаемый формат: images/year/slug/filename или images/year/filename
            if len(parts) >= 3 and parts[0] == 'images':
                try:
                    year = int(parts[1])
                    slug = parts[2] if len(parts) > 3 and parts[2] != os.path.basename(image.file.name) else ''

                    # Обновляем только если отличается
                    if image.year != year or image.slug != slug:
                        old_year, old_slug = image.year, image.slug
                        image.year = year
                        image.slug = slug

                        # Обновляем коллекцию
                        image.collection = image.get_or_create_collection()

                        image.save()
                        updated += 1

                        self.stdout.write(
                            f"Обновлено: {image.title} "
                            f"({old_year}/{old_slug or 'без-категории'} -> {year}/{slug or 'без-категории'})"
                        )
                except (ValueError, IndexError) as e:
                    errors += 1
                    self.stdout.write(
                        self.style.ERROR(f"Ошибка обработки {image.file.name}: {e}")
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nЗавершено! Обновлено: {updated}, ошибок: {errors}"
            )
        )
