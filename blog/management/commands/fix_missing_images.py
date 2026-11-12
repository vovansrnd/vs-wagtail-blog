import os
import re
from django.conf import settings
from django.core.files import File
from django.db import connections
from blog.models import BlogPage, CustomImage

LEGACY_DB = "legacy"
OLD_UPLOADS_PATH = os.path.join(settings.MEDIA_ROOT, "zenway")

LEGACY_DOMAINS = (
    "http://www.zenway.ru", "http://zenway.ru",
    "https://www.zenway.ru", "https://zenway.ru",
)

def dictfetchall(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def normalize_legacy_path(value):
    if not value: return ""
    clean = value.strip()
    for domain in LEGACY_DOMAINS:
        clean = clean.replace(f"{domain}/uploads/", "")
        clean = clean.replace(f"{domain}/", "")
    clean = clean.replace("/media/zenway/", "")
    clean = clean.replace("/uploads/", "")
    if clean.startswith("uploads/"):
        clean = clean[len("uploads/"):]
    clean = clean.replace("/mini/", "/")
    clean = clean.lstrip("/")
    return clean

def get_or_create_image(relative_path):
    if not relative_path: return None
    clean_path = normalize_legacy_path(relative_path)
    if not clean_path: return None
    
    filename = os.path.basename(clean_path)
    existing = CustomImage.objects.filter(title=filename).first()
    if existing:
        return existing
        
    full_local_path = os.path.join(OLD_UPLOADS_PATH, clean_path)
    if not os.path.exists(full_local_path):
        return None
        
    try:
        with open(full_local_path, "rb") as f:
            img = CustomImage(title=filename, year="ZenWay", slug="Imported")
            img.file.save(filename, File(f), save=True)
            img.save()
            return img
    except Exception as e:
        print(f"  [Ошибка сохранения] {filename}: {e}")
        return None

print("="*60)
print("ВОССТАНОВЛЕНИЕ ПОТЕРЯННЫХ ГЛАВНЫХ КАРТИНОК (_pages)")
print("="*60)

# 1. Получаем из старой базы связи "Заголовок статьи -> Путь к картинке"
meta_images_map = {}
with connections[LEGACY_DB].cursor() as cursor:
    cursor.execute("""
        SELECT p.page_title, m.meta_value 
        FROM mso_page p
        JOIN mso_meta m ON p.page_id = m.meta_id_obj
        WHERE p.page_status = 'publish' 
          AND m.meta_table = 'page' 
          AND m.meta_key IN ('image_for_page', 'image')
          AND m.meta_value != ''
    """)
    for row in dictfetchall(cursor):
        meta_images_map[row["page_title"]] = row["meta_value"]

# 2. Ищем страницы Wagtail, у которых нет главной картинки
pages_without_image = BlogPage.objects.filter(main_image__isnull=True)
fixed_count = 0

for page in pages_without_image:
    img_obj = None
    
    # Попытка А: Ищем в старой мета-информации (для папки _pages это основной путь)
    old_img_path = meta_images_map.get(page.title)
    if old_img_path:
        img_obj = get_or_create_image(old_img_path)
        
    # Попытка Б: Если в мете не было, ищем первый тег <img> прямо в тексте (как делал импортёр)
    if not img_obj:
        img_match = re.search(r'<img[^>]+src="([^"]+)"', page.excerpt or "", flags=re.IGNORECASE)
        if not img_match:
            img_match = re.search(r'<img[^>]+src="([^"]+)"', page.body or "", flags=re.IGNORECASE)
            
        if img_match:
            img_obj = get_or_create_image(img_match.group(1))

    # Если картинка нашлась и создалась
    if img_obj:
        page.main_image = img_obj
        
        # Вырезаем дубль этой картинки из текста (чтобы не было 404 ошибки и двойного вывода)
        img_filename = re.escape(img_obj.title)
        pattern = (
            rf"(?:<div[^>]*>\s*)?"
            rf"(?:<p>\s*)?"
            rf"(?:<a[^>]*>\s*)?"
            rf"<img[^>]*{img_filename}[^>]*>"
            rf"(?:\s*</a>)?"
            rf"(?:\s*<br\s*/?>)?"
            rf"(?:\s*</p>)?"
            rf"(?:\s*</div>)?"
        )
        
        if page.excerpt:
            page.excerpt = re.sub(pattern, "", page.excerpt, flags=re.IGNORECASE)
        if page.body:
            page.body = re.sub(pattern, "", page.body, flags=re.IGNORECASE)
            
        page.save()
        page.save_revision().publish()
        
        fixed_count += 1
        print(f"[+] Восстановлена картинка: {page.title} -> {img_obj.title}")

print("="*60)
print(f"Готово! Восстановлено картинок: {fixed_count}")
