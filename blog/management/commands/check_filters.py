from django.core.management.base import BaseCommand
from blog.models import CustomImage


class Command(BaseCommand):
    help = 'Check image filtering'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, help='Filter by year')
        parser.add_argument('--slug', type=str, help='Filter by slug')

    def handle(self, *args, **options):
        year = options.get('year')
        slug = options.get('slug')

        self.stdout.write(f"Filtering by year={year}, slug={slug}")

        # Проверяем фильтрацию
        qs = CustomImage.objects.all()
        self.stdout.write(f"Total images: {qs.count()}")

        if year:
            qs_year = qs.filter(year=year)
            self.stdout.write(f"Images with year={year}: {qs_year.count()}")

            if slug:
                qs_year_slug = qs_year.filter(slug=slug)
                self.stdout.write(f"Images with year={year} and slug={slug}: {qs_year_slug.count()}")

                # Выводим список изображений
                for img in qs_year_slug:
                    self.stdout.write(f"- {img.title} ({img.directory_display})")
