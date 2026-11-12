from django.core.management.base import BaseCommand
from blog.models import CustomImage
from wagtail.models import Collection


class Command(BaseCommand):
    help = 'Fix image collections based on year/slug'

    def handle(self, *args, **options):
        images = CustomImage.objects.all()
        updated = 0

        self.stdout.write(f"Обработка {images.count()} изображений...")

        root_collection = Collection.get_first_root_node()

        for image in images:
            old_collection = image.collection
            new_collection = image.get_or_create_collection()

            if old_collection != new_collection:
                image.collection = new_collection
                image.save(update_fields=['collection'])
                updated += 1

                self.stdout.write(
                    f"Обновлено: {image.title} "
                    f"({old_collection.name} -> {new_collection.name})"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nЗавершено! Обновлено коллекций: {updated}"
            )
        )
