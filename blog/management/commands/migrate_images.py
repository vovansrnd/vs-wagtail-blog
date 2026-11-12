import os
import re
from django.core.management.base import BaseCommand
from django.conf import settings
from blog.models import BlogPage, CustomImage, ImageDirectory
from wagtail.models import Page

def get_clean_safe_filename(target_dir_rel, original_filename, max_length=100):
    clean_name = re.sub(r'^uploads_pages\d*', '', original_filename)
    clean_name = re.sub(r'^uploads', '', clean_name)
    if clean_name.startswith('.'):
        clean_name = 'image' + clean_name
    ext = os.path.splitext(clean_name)[1]
    base = os.path.splitext(clean_name)[0]
    
    available_base_len = max_length - len(target_dir_rel) - 1 - len(ext)
    if available_base_len < len(base):
        base = base[:available_base_len]
    return base + ext

class Command(BaseCommand):
    help = 'Миграция картинок ТОЛЬКО для Zenway'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        limit = options['limit']
        apply_changes = options['apply']

        try:
            zenway_root = Page.objects.get(id=112)
        except Page.DoesNotExist:
            self.stdout.write(self.style.ERROR("Ошибка: Раздел с ID 112 не найден!"))
            return

        pages_qs = BlogPage.objects.descendant_of(zenway_root).live().order_by('-first_published_at')[:limit]

        mode_text = "ФАКТИЧЕСКОЕ ВЫПОЛНЕНИЕ" if apply_changes else "ДЕМО-РЕЖИМ"
        color = self.style.ERROR if apply_changes else self.style.SUCCESS
        self.stdout.write(color(f'\n=== ЗАПУСК: {mode_text} (Лимит: {limit}, Блог: Zenway) ===\n'))

        img_pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

        for page in pages_qs:
            year = str(page.date_published.year)
            slug = page.slug
            target_dir_rel = f"zenway/{year}/{slug}"
            target_dir_abs = os.path.join(settings.MEDIA_ROOT, target_dir_rel)

            self.stdout.write(self.style.WARNING(f'\n📄 СТАТЬЯ: "{page.title}"'))
            
            body_changed = False
            new_body = str(page.body) if page.body else ""

            # 1. ОБРАБОТКА ПОСТЕРА
            if page.main_image:
                img = page.main_image
                old_rel_path = img.file.name 
                
                if target_dir_rel not in old_rel_path:
                    old_abs_path = img.file.path
                    original_filename = os.path.basename(old_abs_path)
                    safe_filename = get_clean_safe_filename(target_dir_rel, original_filename)
                    new_rel_path = f"{target_dir_rel}/{safe_filename}"
                    new_abs_path = os.path.join(settings.MEDIA_ROOT, new_rel_path)

                    if os.path.exists(old_abs_path):
                        self.stdout.write(f"  [ПОСТЕР] Перемещение: {old_rel_path} -> {new_rel_path}")
                        if apply_changes:
                            os.makedirs(target_dir_abs, exist_ok=True)
                            ImageDirectory.objects.get_or_create(year=year, slug=slug)
                            os.rename(old_abs_path, new_abs_path)
                            img.file.name = new_rel_path
                            img.year = year
                            img.slug = slug
                            img.title = safe_filename
                            img.save()
                    else:
                        self.stdout.write(self.style.ERROR(f"  [ПОСТЕР] Файл не найден: {old_abs_path}"))

            # 2. ОБРАБОТКА КАРТИНОК В ТЕКСТЕ
            if page.body:
                urls = img_pattern.findall(new_body)
                for url in urls:
                    local_url = url.replace(settings.BASE_URL, '').split('?')[0]
                    if local_url.startswith('http'):
                        continue
                    
                    old_rel_path = local_url.replace(settings.MEDIA_URL, '', 1)
                    old_abs_path = os.path.join(settings.MEDIA_ROOT, old_rel_path)
                    if target_dir_rel in old_rel_path:
                        continue

                    original_filename = os.path.basename(old_abs_path)
                    safe_filename = get_clean_safe_filename(target_dir_rel, original_filename)
                    new_rel_path = f"{target_dir_rel}/{safe_filename}"
                    new_abs_path = os.path.join(settings.MEDIA_ROOT, new_rel_path)
                    new_full_url = f"{settings.MEDIA_URL}{new_rel_path}"

                    if os.path.exists(old_abs_path):
                        self.stdout.write(f"  [ТЕКСТ] Обработка: {old_rel_path} -> {new_rel_path}")
                        if apply_changes:
                            os.makedirs(target_dir_abs, exist_ok=True)
                            ImageDirectory.objects.get_or_create(year=year, slug=slug)
                            
                            if os.path.exists(new_abs_path) and old_abs_path != new_abs_path:
                                os.remove(new_abs_path)
                                
                            os.rename(old_abs_path, new_abs_path)

                            # ЗАЩИТА ОТ БИТЫХ КАРТИНОК
                            try:
                                CustomImage.objects.get_or_create(
                                    file=new_rel_path,
                                    defaults={
                                        'title': safe_filename,
                                        'year': year,
                                        'slug': slug
                                    }
                                )
                                new_body = new_body.replace(url, new_full_url)
                                body_changed = True
                            except Exception as e:
                                self.stdout.write(self.style.ERROR(f"  [ОШИБКА WAGTAIL] Файл битый: {safe_filename}. Оставляем как было."))
                                # Возвращаем файл обратно на диск
                                os.rename(new_abs_path, old_abs_path)
                    else:
                        self.stdout.write(self.style.ERROR(f"  [ТЕКСТ] Файл не найден: {old_rel_path}"))

            # 3. СОХРАНЕНИЕ СТАТЬИ
            if apply_changes and body_changed:
                page.body = new_body
                page.save(update_fields=['body'])
                self.stdout.write(self.style.SUCCESS("  ✓ Текст обновлен"))

        self.stdout.write(self.style.SUCCESS('\n=== ГОТОВО ==='))
