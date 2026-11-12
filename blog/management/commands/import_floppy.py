import os
import re
from datetime import datetime

import pytz
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import connections, transaction
from django.utils import timezone
from django.utils.text import slugify

from blog.models import BlogIndexPage, BlogPage, Category, Comment, CustomImage

LEGACY_DB = "legacy"
TZ = pytz.timezone(settings.TIME_ZONE)
OLD_UPLOADS_PATH = os.path.join(settings.MEDIA_ROOT, "floppy")


class Command(BaseCommand):
    help = "Импорт данных из Floppy Games в Wagtail"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Удалить старые импортированные записи перед стартом",
        )

    def dictfetchall(self, cursor):
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def parse_bb_codes(self, text, is_comment=False):
        if not text:
            return ""

        if is_comment:
            # Сначала убираем лишние пробелы по краям
            text = text.strip()
            # Заменяем двойные \n на два <br> (или параграфы)
            text = (
                text.replace("\r\n", "\n")
                .replace("\n\n", "</p><p>")
                .replace("\n", "<br>")
            )
            # Оборачиваем весь текст в <p>
            text = f"<p>{text}</p>"

        # Базовые теги
        text = re.sub(
            r"\[b\](.*?)\[/b\]",
            r"<strong>\1</strong>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[bold\](.*?)\[/bold\]",
            r"<strong>\1</strong>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[h3\](.*?)\[/h3\]",
            r"<h3>\1</h3>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[h2\](.*?)\[/h2\]",
            r"<h2>\1</h2>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[i\](.*?)\[/i\]", r"<em>\1</em>", text, flags=re.IGNORECASE | re.DOTALL
        )
        text = re.sub(
            r"\[u\](.*?)\[/u\]", r"<u>\1</u>", text, flags=re.IGNORECASE | re.DOTALL
        )
        text = re.sub(
            r"\[s\](.*?)\[/s\]",
            r"<strike>\1</strike>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[quote\](.*?)\[/quote\]",
            r"<blockquote>\1</blockquote>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[center\](.*?)\[/center\]",
            r'<div style="text-align: center;">\1</div>',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Ссылки
        text = re.sub(
            r"\[url=(.*?)\](.*?)\[/url\]",
            r'<a href="\1">\2</a>',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[url\](.*?)\[/url\]",
            r'<a href="\1">\1</a>',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        text = re.sub(
            r"\[(https?://[^\]]+|/[^\]]+)\](.*?)\[/url\]",
            r'<a href="\1">\2</a>',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Изображения (если вдруг где-то были BB-кодами)
        text = re.sub(
            r"\[img\s+(.*?)\](.*?)\[/img\]",
            r'<img src="\2" alt="\1" loading="lazy">',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[img\](.*?)\[/img\]",
            r'<img src="\1" alt="" loading="lazy">',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # ФИКС ПУТЕЙ ДЛЯ КАРТИНОК ИЗ ЧИСТОГО HTML
        # Меняем домен на путь к изолированной папке (с учетом вложенной uploads)
        text = text.replace("http://www.floppygames.ru/uploads/", "/media/floppy/")
        text = text.replace("http://floppygames.ru/uploads/", "/media/floppy/")
        text = text.replace("https://floppygames.ru/uploads/", "/media/floppy/")
        text = text.replace("https://www.floppygames.ru/uploads/", "/media/floppy/")
        text = text.replace("http://www.floppygames.ru/page/", "/")
        text = text.replace("https://www.floppygames.ru/page/", "/")

        text = text.replace('href="/page/', 'href="/')  # Фикс старых внутренних ссылок
        text = text.replace("/mini/", "/")

        text = text.replace('src="/uploads/', 'src="/media/floppy/')
        text = text.replace('href="/uploads/', 'href="/media/floppy/')

        text = text.replace("http://floppygames.ru/page/", "/")
        text = text.replace("https://floppygames.ru/page/", "/")
        text = text.replace("http://floppygames.ru/tag/", "/tag/")
        text = text.replace("https://floppygames.ru/tag/", "/tag/")
        text = text.replace("http://floppygames.ru/category/", "/category/")
        text = text.replace("https://floppygames.ru/category/", "/category/")
        text = text.replace("http://www.floppygames.ru/tag/", "/tag/")
        text = text.replace("https://www.floppygames.ru/tag/", "/tag/")
        text = text.replace("http://www.floppygames.ru/category/", "/category/")
        text = text.replace("https://www.floppygames.ru/category/", "/category/")

        return text

    def get_or_create_image(self, relative_path):
        if not relative_path:
            return None
        # --- ОЧИСТКА МУСОРА ПЕРЕД ПОИСКОМ ФАЙЛА ---
        clean_path = relative_path
        clean_path = clean_path.replace("http://www.floppygames.ru/uploads/", "")
        clean_path = clean_path.replace("http://www.floppygames.ru/", "")
        clean_path = clean_path.replace("http://floppygames.ru/uploads/", "")
        clean_path = clean_path.replace("http://floppygames.ru/", "")
        clean_path = clean_path.replace("https://www.floppygames.ru/uploads/", "")
        clean_path = clean_path.replace("https://www.floppygames.ru/", "")
        clean_path = clean_path.replace("https://floppygames.ru/uploads/", "")
        clean_path = clean_path.replace("https://floppygames.ru/", "")
        clean_path = clean_path.replace("/mini/", "/")
        # Убираем uploads/ если остался после очистки домена
        if clean_path.startswith("uploads/"):
            clean_path = clean_path[len("uploads/") :]
        clean_path = clean_path.lstrip("/")
        # ------------------------------------------
        filename = os.path.basename(relative_path)
        existing = CustomImage.objects.filter(title=filename).first()
        if existing:
            return existing

        # ФИКС 1: Убираем ведущий слеш, чтобы os.path.join работал правильно!
        clean_path = relative_path.lstrip("/")
        full_local_path = os.path.join(OLD_UPLOADS_PATH, clean_path)

        if os.path.exists(full_local_path):
            try:
                with open(full_local_path, "rb") as f:
                    # ФИКС 2: Кладем картинки в корневую коллекцию "FloppyGames"
                    img = CustomImage(
                        title=filename, year="FloppyGames", slug="Imported"
                    )
                    img.file.save(filename, File(f), save=True)
                    img.save()
                    return img
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f"Ошибка сохранения картинки {filename}: {e}")
                )
        else:
            self.stdout.write(
                self.style.WARNING(f"Файл не найден на диске: {full_local_path}")
            )
        return None

    @transaction.atomic
    def handle(self, *args, **options):
        settings.COMMENTS_NOTIFY_ADMINS = False

        User = get_user_model()
        admin_user = User.objects.get(id=1)

        blog_index = BlogIndexPage.objects.get(slug="floppy-home")

        if options["clean"]:
            self.stdout.write(self.style.WARNING("Очистка песочницы..."))
            BlogPage.objects.child_of(blog_index).delete()

        cat_map = {}
        page_map = {}
        comment_map = {}

        with connections[LEGACY_DB].cursor() as cursor:
            # === 1. КАТЕГОРИИ ===
            self.stdout.write("1. Импорт категорий...")
            cursor.execute("SELECT * FROM floppygames_category")
            for row in self.dictfetchall(cursor):
                slug = row["category_slug"] or slugify(
                    row["category_name"], allow_unicode=True
                )
                cat, _ = Category.objects.update_or_create(
                    slug=slug, defaults={"name": row["category_name"]}
                )
                cat_map[row["category_id"]] = cat

            cursor.execute("SELECT * FROM floppygames_category")
            for row in self.dictfetchall(cursor):
                if row["category_id_parent"]:
                    child = cat_map.get(row["category_id"])
                    parent = cat_map.get(row["category_id_parent"])
                    if child and parent:
                        child.parent = parent
                        child.save()
            Category.objects.rebuild()

            # === 2. СТАТЬИ ===
            self.stdout.write("\n2. Импорт статей...")
            cursor.execute("""
                SELECT page_id, page_title, page_slug, page_content, page_date_publish
                FROM floppygames_page WHERE page_status = 'publish'
            """)

            for p_data in self.dictfetchall(cursor):
                pid = p_data["page_id"]
                title = p_data["page_title"]
                slug = p_data["page_slug"] or slugify(title, allow_unicode=True)

                date_pub = p_data["page_date_publish"]
                if isinstance(date_pub, str):
                    date_pub = datetime.fromisoformat(date_pub)
                if timezone.is_naive(date_pub):
                    date_pub = timezone.make_aware(date_pub, TZ)

                raw_content = p_data["page_content"]
                cut_match = re.search(r"\[x?cut\]", raw_content, flags=re.IGNORECASE)

                if cut_match:
                    raw_excerpt = raw_content[: cut_match.start()]
                    raw_body = raw_content.replace(cut_match.group(0), "")
                else:
                    raw_excerpt = ""
                    raw_body = raw_content

                clean_excerpt = self.parse_bb_codes(raw_excerpt)
                clean_body = self.parse_bb_codes(raw_body)

                new_page = BlogPage(
                    title=title,
                    slug=slug,
                    excerpt=clean_excerpt,
                    body=clean_body,
                    date_published=date_pub,
                    first_published_at=date_pub,
                    owner=admin_user,
                    live=True,
                )
                # Добавляем в дерево (базовое сохранение)
                blog_index.add_child(instance=new_page)
                page_map[pid] = new_page

                # === 1. Читаем мета-данные (Теги и картинка, если она была задана явно) ===
                cursor.execute(
                    "SELECT meta_key, meta_value FROM floppygames_meta WHERE meta_table='page' AND meta_id_obj=%s",
                    [pid],
                )
                for m in self.dictfetchall(cursor):
                    if m["meta_key"] == "tags" and m["meta_value"]:
                        tags_raw = [
                            t.strip() for t in m["meta_value"].split(",") if t.strip()
                        ]
                        new_page.tags.add(*tags_raw)
                    if m["meta_key"] in ["image_for_page", "image"] and m["meta_value"]:
                        img_obj = self.get_or_create_image(m["meta_value"])
                        if img_obj:
                            new_page.main_image = img_obj

                # === 2. ЕСЛИ КАРТИНКИ НЕТ В МЕТЕ, ИЩЕМ ЕЁ В ТЕКСТЕ ===
                if not new_page.main_image:
                    # Ищем первый тег <img> в анонсе, а если там нет - в теле статьи
                    img_match = re.search(
                        r'<img[^>]+src="([^"]+)"', new_page.excerpt, flags=re.IGNORECASE
                    )
                    if not img_match:
                        img_match = re.search(
                            r'<img[^>]+src="([^"]+)"',
                            new_page.body,
                            flags=re.IGNORECASE,
                        )

                    if img_match:
                        img_src = img_match.group(
                            1
                        )  # Получаем: "/media/floppy/01_2010/ugh8.gif"

                        # Превращаем URL в относительный путь для функции: "01_2010/ugh8.gif"
                        relative_src = img_src.replace("/media/floppy/", "")

                        img_obj = self.get_or_create_image(relative_src)
                        if img_obj:
                            new_page.main_image = img_obj

                # === 3. УДАЛЯЕМ ГЛАВНУЮ КАРТИНКУ ИЗ ТЕКСТА (чтобы не было дубля) ===
                if new_page.main_image:
                    img_filename = re.escape(new_page.main_image.title)

                    # Я немного улучшил твою регулярку: добавил обработку тега <p>
                    # и возможного <br>, который я заметил в твоем куске HTML из базы.
                    pattern = rf"(?:<div[^>]*>\s*)?(?:<p>\s*)?(?:<a[^>]*>\s*)?<img[^>]*{img_filename}[^>]*>(?:\s*</a>)?(?:\s*<br\s*/?>)?(?:\s*</p>)?(?:\s*</div>)?"

                    new_page.excerpt = re.sub(
                        pattern, "", new_page.excerpt, flags=re.IGNORECASE
                    )
                    new_page.body = re.sub(
                        pattern, "", new_page.body, flags=re.IGNORECASE
                    )

                # === 4. Рубрики ===
                cursor.execute(
                    "SELECT category_id FROM floppygames_cat2obj WHERE page_id=%s",
                    [pid],
                )
                for c in self.dictfetchall(cursor):
                    if c["category_id"] in cat_map:
                        new_page.categories.add(cat_map[c["category_id"]])

                # Теперь сохраняем связи в БД и делаем ПУБЛИКАЦИЮ РЕВИЗИИ (чтобы админка всё увидела)
                new_page.save()
                new_page.save_revision().publish()
                new_page.save_revision().publish()

                self.stdout.write(f"  + Импортировано: {title}")

            # === 3. КОММЕНТАРИИ ===
            self.stdout.write("\n3. Импорт комментариев...")
            cursor.execute("SELECT comusers_id, comusers_nik FROM floppygames_comusers")
            comusers_map = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("""
                SELECT comments_id, comments_page_id, comments_users_id, comments_comusers_id, 
                       comments_author_name, comments_date, comments_content, comments_parent_id
                FROM floppygames_comments
                WHERE comments_approved = 1
                ORDER BY comments_id ASC
            """)
            comments = self.dictfetchall(cursor)

            for c in comments:
                old_pid = c["comments_page_id"]
                if old_pid not in page_map:
                    continue

                user_id = c["comments_users_id"]
                comuser_id = c["comments_comusers_id"]
                author_name = c["comments_author_name"]
                user_obj = None

                if user_id == 1:
                    user_obj = admin_user
                    author_name = admin_user.get_full_name() or admin_user.username
                elif comuser_id != 0:
                    author_name = comusers_map.get(comuser_id, "Пользователь")
                elif not author_name:
                    author_name = "Аноним"

                c_date = c["comments_date"]
                if timezone.is_naive(c_date):
                    c_date = timezone.make_aware(c_date, TZ)

                clean_comment = self.parse_bb_codes(
                    c["comments_content"], is_comment=True
                )

                new_comm = Comment(
                    page=page_map[old_pid],
                    user=user_obj,
                    author_name=author_name,
                    content=clean_comment,
                    date_created=c_date,
                    is_approved=True,
                    parent=None,
                )
                new_comm.save()
                comment_map[c["comments_id"]] = new_comm

            for c in comments:
                old_cid = c["comments_id"]
                old_parent_id = c["comments_parent_id"]
                if (
                    old_parent_id
                    and old_parent_id in comment_map
                    and old_cid in comment_map
                ):
                    child = comment_map[old_cid]
                    child.parent = comment_map[old_parent_id]
                    child.save()

            self.stdout.write("  - Перестроение MPTT дерева...")
            Comment.objects.rebuild()

        self.stdout.write(self.style.SUCCESS("\nИмпорт Floppy Games успешно завершен!"))
