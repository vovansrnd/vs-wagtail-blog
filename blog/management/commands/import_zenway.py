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

BLOG_INDEX_SLUG = "zw_page"
OLD_UPLOADS_PATH = os.path.join(settings.MEDIA_ROOT, "zenway")

LEGACY_DOMAINS = (
    "http://www.zenway.ru",
    "http://zenway.ru",
    "https://www.zenway.ru",
    "https://zenway.ru",
)


class Command(BaseCommand):
    help = "Импорт данных из ZenWay (MaxSite CMS) в Wagtail"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Удалить старые импортированные записи перед стартом",
        )

    def dictfetchall(self, cursor):
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def make_valid_slug(self, raw_slug, title, obj_id, used_slugs=None, prefix="page"):
        source = (raw_slug or title or "").strip()
        base = slugify(source, allow_unicode=True)

        if not base:
            base = f"{prefix}-{obj_id}"

        slug = base
        if used_slugs is not None:
            counter = 2
            while slug in used_slugs:
                slug = f"{base}-{counter}"
                counter += 1
            used_slugs.add(slug)

        return slug

    def make_aware_dt(self, value, fallback=None):
        if fallback is None:
            fallback = timezone.now()

        if not value:
            return fallback

        try:
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            if timezone.is_naive(value):
                value = timezone.make_aware(value, TZ)
            return value
        except Exception:
            return fallback

    def normalize_legacy_path(self, value):
        if not value:
            return ""

        clean = value.strip()

        for domain in LEGACY_DOMAINS:
            clean = clean.replace(f"{domain}/uploads/", "")
            clean = clean.replace(f"{domain}/", "")

        clean = clean.replace("/media/zenway/", "")
        clean = clean.replace("/uploads/", "")
        if clean.startswith("uploads/"):
            clean = clean[len("uploads/") :]

        clean = clean.replace("/mini/", "/")
        clean = clean.lstrip("/")

        return clean

    def rewrite_legacy_urls(self, text):
        if not text:
            return ""

        for domain in LEGACY_DOMAINS:
            text = text.replace(f"{domain}/uploads/", "/media/zenway/")
            text = text.replace(f"{domain}/page/", "/")
            text = text.replace(f"{domain}/tag/", "/tag/")
            text = text.replace(f"{domain}/category/", "/category/")

        text = text.replace('href="/page/', 'href="/')
        text = text.replace('src="/uploads/', 'src="/media/zenway/')
        text = text.replace('href="/uploads/', 'href="/media/zenway/')
        text = text.replace("/mini/", "/")

        return text

    def parse_bb_codes(self, text, is_comment=False):
        if not text:
            return ""

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        code_blocks = []

        def stash_code(match):
            idx = len(code_blocks)
            code_blocks.append(match.group(1) or "")
            return f"@@CODEBLOCK_{idx}@@"

        def restore_code_blocks(value):
            def repl(match):
                idx = int(match.group(1))
                body = code_blocks[idx]
                body = body.replace("<", "&lt;").replace(">", "&gt;")
                return f"<pre>{body}</pre>"

            return re.sub(r"@@CODEBLOCK_(\d+)@@", repl, value)

        text = re.sub(
            r"\[pre\](.*?)\[/pre\]",
            stash_code,
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"<pre[^>]*>(.*?)</pre>",
            stash_code,
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if is_comment:
            text = text.strip()
            text = text.replace("\n\n", "</p><p>").replace("\n", "<br>")
            text = f"<p>{text}</p>"

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
            r"\[i\](.*?)\[/i\]",
            r"<em>\1</em>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[u\](.*?)\[/u\]",
            r"<u>\1</u>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"\[s\](.*?)\[/s\]",
            r"<strike>\1</strike>",
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
        text = re.sub(
            r"\[pcenter\](.*?)\[/pcenter\]",
            r'<div style="text-align: center;">\1</div>',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        text = re.sub(
            r"\[spoiler\](.*?)\[/spoiler\]",
            r"<details><summary>Развернуть</summary>\1</details>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

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
        text = re.sub(
            r"\[image=([^\]]+)\](.*?)\[/image\]",
            r'<a href="\2" target="_blank"><img src="\1" loading="lazy"></a>',
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        text = self.rewrite_legacy_urls(text)
        text = restore_code_blocks(text)

        return text

    def get_or_create_image(self, relative_path):
        if not relative_path:
            return None

        clean_path = self.normalize_legacy_path(relative_path)
        if not clean_path:
            return None

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
            self.stdout.write(
                self.style.WARNING(f"Ошибка сохранения картинки {filename}: {e}")
            )
            return None

    def handle(self, *args, **options):
        settings.COMMENTS_NOTIFY_ADMINS = False

        User = get_user_model()
        admin_user = User.objects.get(id=1)
        blog_index = BlogIndexPage.objects.get(slug=BLOG_INDEX_SLUG)

        if options["clean"]:
            self.stdout.write(self.style.WARNING("Очистка песочницы..."))
            BlogPage.objects.child_of(blog_index).delete()

        self.stdout.write("Анализ пользователей для автоматического сопоставления...")

        wagtail_users = list(User.objects.all())
        wt_emails = {u.email.strip().lower(): u for u in wagtail_users if u.email}
        wt_usernames = {
            u.username.strip().lower(): u for u in wagtail_users if u.username
        }

        def find_wt_user(email, nik, login=""):
            if email:
                email_key = email.strip().lower()
                if email_key in wt_emails:
                    return wt_emails[email_key]
            for name in [nik, login]:
                if name:
                    name_key = name.strip().lower()
                    if name_key in wt_usernames:
                        return wt_usernames[name_key]
            return None

        cat_map = {}
        page_map = {}
        comment_map = {}
        authors_map = {}
        comusers_map = {}

        with connections[LEGACY_DB].cursor() as cursor:
            # =========================
            # Авторы и комментаторы
            # =========================
            cursor.execute(
                "SELECT users_id, users_email, users_nik, users_login FROM mso_users"
            )
            for row in self.dictfetchall(cursor):
                wt_user = find_wt_user(
                    row["users_email"], row["users_nik"], row["users_login"]
                )
                display_name = row["users_nik"] or row["users_login"] or "Аноним"
                authors_map[row["users_id"]] = (wt_user, display_name)

            cursor.execute(
                "SELECT comusers_id, comusers_email, comusers_nik FROM mso_comusers"
            )
            for row in self.dictfetchall(cursor):
                wt_user = find_wt_user(row["comusers_email"], row["comusers_nik"])
                display_name = row["comusers_nik"] or "Гость"
                comusers_map[row["comusers_id"]] = (wt_user, display_name)

            # =========================
            # Категории
            # =========================
            self.stdout.write("\n1. Импорт категорий...")

            cursor.execute("""
                SELECT category_id, category_id_parent, category_type, category_name, category_slug
                FROM mso_category
                WHERE category_type = 'page'
                ORDER BY category_id ASC
            """)
            cat_rows = self.dictfetchall(cursor)

            for row in cat_rows:
                slug = self.make_valid_slug(
                    row["category_slug"],
                    row["category_name"],
                    row["category_id"],
                    used_slugs=None,
                    prefix="category",
                )
                cat, _ = Category.objects.update_or_create(
                    slug=slug,
                    defaults={"name": row["category_name"]},
                )
                cat_map[row["category_id"]] = cat

            imported_cat_ids = [cat.pk for cat in cat_map.values()]
            if imported_cat_ids:
                Category.objects.filter(pk__in=imported_cat_ids).update(parent=None)

            self.stdout.write("  - Выстраиваем иерархию категорий...")

            cat_parent_graph = {}
            for row in cat_rows:
                cid = row["category_id"]
                pid = row["category_id_parent"]
                if pid and pid != 0 and cid in cat_map and pid in cat_map:
                    cat_parent_graph[cid] = pid

            def cat_has_cycle(start_id, parent_id):
                visited = set()
                current = parent_id
                while current is not None:
                    if current == start_id:
                        return True
                    if current in visited:
                        break
                    visited.add(current)
                    current = cat_parent_graph.get(current)
                return False

            for row in cat_rows:
                cid = row["category_id"]
                pid = row["category_id_parent"]

                if not pid or pid == 0:
                    continue
                if cid not in cat_map or pid not in cat_map:
                    continue
                if cid == pid:
                    self.stdout.write(
                        self.style.WARNING(
                            f"    ! Категория {cid} ссылается сама на себя."
                        )
                    )
                    continue
                if cat_has_cycle(cid, pid):
                    self.stdout.write(
                        self.style.WARNING(
                            f"    ! Кольцевая ссылка в категории {cid} -> {pid}."
                        )
                    )
                    continue

                Category.objects.filter(pk=cat_map[cid].pk).update(
                    parent_id=cat_map[pid].pk
                )

            Category.objects.rebuild()
            self.stdout.write(
                self.style.SUCCESS(f"  Категорий импортировано: {len(cat_map)}")
            )

            # =========================
            # Статьи
            # =========================
            self.stdout.write("\n2. Импорт статей...")

            used_page_slugs = set(
                BlogPage.objects.child_of(blog_index).values_list("slug", flat=True)
            )

            cursor.execute("""
                SELECT page_id, page_type_id, page_id_autor, page_title, page_slug,
                       page_content, page_content2, page_date_publish
                FROM mso_page
                WHERE page_status = 'publish'
                ORDER BY page_id ASC
            """)
            page_rows = self.dictfetchall(cursor)
            total_pages = len(page_rows)

            self.stdout.write(f"  Всего статей для импорта: {total_pages}")

            for page_num, p_data in enumerate(page_rows, start=1):
                pid = p_data["page_id"]
                title = p_data["page_title"] or f"Page {pid}"

                try:
                    with transaction.atomic():
                        raw_slug = p_data["page_slug"]
                        slug = self.make_valid_slug(
                            raw_slug,
                            title,
                            pid,
                            used_slugs=used_page_slugs,
                            prefix="page",
                        )

                        if raw_slug and raw_slug != slug:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'  ! Slug: "{raw_slug}" -> "{slug}" (page_id={pid})'
                                )
                            )

                        author_data = authors_map.get(p_data["page_id_autor"])
                        page_owner = (
                            author_data[0]
                            if (author_data and author_data[0])
                            else admin_user
                        )

                        date_pub = self.make_aware_dt(p_data["page_date_publish"])

                        raw_content = p_data["page_content"] or ""
                        if p_data["page_content2"]:
                            raw_content += "\n" + p_data["page_content2"]

                        cut_match = re.search(
                            r"(\[x?cut\]|<cut>)",
                            raw_content,
                            flags=re.IGNORECASE,
                        )

                        if cut_match:
                            raw_excerpt = raw_content[: cut_match.start()]
                            raw_body = (
                                raw_content[: cut_match.start()]
                                + raw_content[cut_match.end() :]
                            )
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
                            owner=page_owner,
                            live=True,
                        )

                        blog_index.add_child(instance=new_page)

                        page_tags = set()
                        if p_data["page_type_id"] == 3:
                            page_tags.add("mini")

                        cursor.execute(
                            "SELECT meta_key, meta_value FROM mso_meta WHERE meta_table='page' AND meta_id_obj=%s",
                            [pid],
                        )
                        meta_rows = self.dictfetchall(cursor)

                        for m in meta_rows:
                            meta_key = m["meta_key"]
                            meta_value = m["meta_value"]

                            if meta_key == "tags" and meta_value:
                                tags_raw = [
                                    t.strip()
                                    for t in re.split(r"[,\n]+", meta_value)
                                    if t.strip()
                                ]
                                page_tags.update(tags_raw)

                            if (
                                meta_key in ["image_for_page", "image"]
                                and meta_value
                                and not new_page.main_image
                            ):
                                img_obj = self.get_or_create_image(meta_value)
                                if img_obj:
                                    new_page.main_image = img_obj

                        if page_tags:
                            new_page.tags.add(*sorted(page_tags))

                        if not new_page.main_image:
                            img_match = re.search(
                                r'<img[^>]+src="([^"]+)"',
                                new_page.excerpt,
                                flags=re.IGNORECASE,
                            )
                            if not img_match:
                                img_match = re.search(
                                    r'<img[^>]+src="([^"]+)"',
                                    new_page.body,
                                    flags=re.IGNORECASE,
                                )
                            if img_match:
                                img_src = img_match.group(1)
                                relative_src = self.normalize_legacy_path(img_src)
                                img_obj = self.get_or_create_image(relative_src)
                                if img_obj:
                                    new_page.main_image = img_obj

                        if new_page.main_image:
                            img_filename = re.escape(new_page.main_image.title)
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
                            new_page.excerpt = re.sub(
                                pattern,
                                "",
                                new_page.excerpt,
                                flags=re.IGNORECASE,
                            )
                            new_page.body = re.sub(
                                pattern,
                                "",
                                new_page.body,
                                flags=re.IGNORECASE,
                            )

                        cursor.execute(
                            "SELECT category_id FROM mso_cat2obj WHERE page_id=%s",
                            [pid],
                        )
                        for c in self.dictfetchall(cursor):
                            if c["category_id"] in cat_map:
                                new_page.categories.add(cat_map[c["category_id"]])

                        new_page.save()
                        revision = new_page.save_revision()
                        revision.publish()

                        page_map[pid] = new_page

                        if page_num % 50 == 0 or page_num == total_pages:
                            self.stdout.write(f"  [{page_num}/{total_pages}] {title}")

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  ! Ошибка page_id={pid} ({title}): {e}")
                    )

            self.stdout.write(
                self.style.SUCCESS(f"  Статей импортировано: {len(page_map)}")
            )

            # =========================
            # Комментарии
            # =========================
            self.stdout.write("\n3. Импорт комментариев...")

            cursor.execute("""
                SELECT comments_id, comments_page_id, comments_users_id, comments_comusers_id,
                       comments_author_name, comments_date, comments_content, comments_parent_id
                FROM mso_comments
                WHERE comments_approved = 1
                ORDER BY comments_id ASC
            """)
            comments = self.dictfetchall(cursor)
            total_comments = len(comments)

            self.stdout.write(f"  Всего комментариев для импорта: {total_comments}")

            for i, c in enumerate(comments, start=1):
                old_pid = c["comments_page_id"]
                old_cid = c["comments_id"]

                if old_pid not in page_map:
                    continue

                try:
                    user_id = c["comments_users_id"]
                    comuser_id = c["comments_comusers_id"]

                    user_obj = None
                    author_name = c["comments_author_name"] or ""

                    if user_id > 0:
                        a_data = authors_map.get(user_id)
                        if a_data:
                            user_obj = a_data[0]
                            author_name = a_data[1]
                    elif comuser_id > 0:
                        c_data = comusers_map.get(comuser_id)
                        if c_data:
                            user_obj = c_data[0]
                            author_name = c_data[1]

                    if user_obj:
                        author_name = user_obj.get_full_name() or user_obj.username
                    elif not author_name:
                        author_name = "Аноним"

                    c_date = self.make_aware_dt(
                        c["comments_date"],
                        fallback=page_map[old_pid].date_published,
                    )

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
                    comment_map[old_cid] = new_comm

                    if i % 200 == 0:
                        self.stdout.write(
                            f"  [{i}/{total_comments}] comment_id={old_cid}, автор: {author_name}"
                        )

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  ! Ошибка comment_id={old_cid}: {e}")
                    )

            self.stdout.write(
                self.style.SUCCESS(f"  Комментариев импортировано: {len(comment_map)}")
            )

            # =========================
            # Иерархия комментариев
            # =========================
            self.stdout.write("  - Выстраиваем иерархию комментариев...")

            parent_graph = {}
            for c in comments:
                old_cid = c["comments_id"]
                old_parent_id = c["comments_parent_id"]
                if (
                    old_parent_id
                    and old_cid in comment_map
                    and old_parent_id in comment_map
                    and old_cid != old_parent_id
                ):
                    parent_graph[old_cid] = old_parent_id

            def has_cycle(start_id, parent_id):
                visited = set()
                current = parent_id
                while current is not None:
                    if current == start_id:
                        return True
                    if current in visited:
                        break
                    visited.add(current)
                    current = parent_graph.get(current)
                return False

            linked = 0
            skipped = 0

            for c in comments:
                old_cid = c["comments_id"]
                old_parent_id = c["comments_parent_id"]

                if not old_parent_id:
                    continue
                if old_cid not in comment_map or old_parent_id not in comment_map:
                    continue
                if old_cid == old_parent_id:
                    self.stdout.write(
                        self.style.WARNING(
                            f"    ! Комментарий {old_cid} ссылается сам на себя."
                        )
                    )
                    skipped += 1
                    continue
                if has_cycle(old_cid, old_parent_id):
                    self.stdout.write(
                        self.style.WARNING(f"    ! Цикл: {old_cid} -> {old_parent_id}.")
                    )
                    skipped += 1
                    continue

                Comment.objects.filter(pk=comment_map[old_cid].pk).update(
                    parent_id=comment_map[old_parent_id].pk
                )
                linked += 1

            self.stdout.write(
                f"  Связей parent установлено: {linked}, пропущено: {skipped}"
            )

            self.stdout.write("  - Перестроение MPTT дерева комментариев...")
            Comment.objects.rebuild()

        self.stdout.write(self.style.SUCCESS("\nИмпорт ZenWay успешно завершен!"))
