from django.core.management.base import BaseCommand
from blog.models import CustomImage
import os
from pathlib import Path


class Command(BaseCommand):
    help = 'Check images data and sync with filesystem'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Fix image year/slug based on file path',
        )

    def handle(self, *args, **options):
        self.stdout.write("Проверка изображений в базе данных:")

        # Показываем текущее состояние
        images = CustomImage.objects.all()
        self.stdout.write(f"\nВсего изображений в БД: {images.count()}")

        # Группируем по year/slug
        from django.db.models import Count
        stats = images.values('year', 'slug').annotate(count=Count('id')).order_by('year', 'slug')

        self.stdout.write("\nРаспределение по year/slug:")
        for stat in stats:
            slug_display = stat['slug'] or '<пусто>'
            self.stdout.write(f"  {stat['year']}/{slug_display}: {stat['count']} изображений")

        # Проверяем файлы
        self.stdout.write("\n\nПроверка файлов:")
        for image in images[:10]:  # Первые 10 для примера
            self.stdout.write(f"\nID: {image.id}")
            self.stdout.write(f"  Title: {image.title}")
            self.stdout.write(f"  Year: {image.year}")
            self.stdout.write(f"  Slug: '{image.slug}'")
            self.stdout.write(f"  File: {image.file.name}")

            # Пытаемся извлечь year/slug из пути файла
            if image.file and image.file.name:
                parts = image.file.name.split('/')
                if len(parts) >= 3 and parts[0] == 'images':
                    detected_year = parts[1]
                    detected_slug = parts[2] if len(parts) > 3 else ''

                    if options['fix']:
                        if str(image.year) != detected_year or image.slug != detected_slug:
                            self.stdout.write(self.style.WARNING(
                                f"  Обновляем: year={detected_year}, slug={detected_slug}"
                            ))
                            try:
                                image.year = int(detected_year)
                                image.slug = detected_slug
                                image.save()
                                self.stdout.write(self.style.SUCCESS("  Обновлено!"))
                            except ValueError:
                                self.stdout.write(self.style.ERROR(f"  Ошибка: неверный год {detected_year}"))
                    else:
                        self.stdout.write(f"  Определено из пути: year={detected_year}, slug={detected_slug}")

        if not options['fix']:
            self.stdout.write(self.style.WARNING(
                "\n\nДля исправления данных запустите команду с флагом --fix"
            ))
