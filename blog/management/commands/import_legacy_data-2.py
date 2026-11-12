# blog/management/commands/import_data.py (или как у вас называется)

import os
from urllib.parse import urlparse
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils.text import slugify
from django.core.files import File
from django.conf import settings
from django.contrib.auth import get_user_model  # <-- Импортируйте для доступа к модели User

from wagtail.models import Page
from blog.models import BlogPage, Category, Comment, CustomImage  # Убедитесь, что Comment импортирован


# from your_app.models import SearchPage # Если нужна ссылка на SearchPage, но не для миграции комментариев

def dictfetchall(cursor):
    """Возвращает все строки из курсора в виде словаря."""
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


class Command(BaseCommand):
    help = 'Импорт данных из старой базы данных MaxSite CMS в Wagtail'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Начинаем миграцию данных..."))

        User = get_user_model()  # Получаем модель пользователя

        try:
            # ИЩЕМ НАШУ НОВУЮ ПРОМЕЖУТОЧНУЮ СТРАНИЦУ СО СЛАГОМ 'page'
            parent_page = Page.objects.get(slug='page')
            # АДМИН САЙТА - ЕДИНСТВЕННЫЙ ПОЛЬЗОВАТЕЛЬ С ID = 1 В AUTH_USER
            admin_user = User.objects.get(id=1)  # <-- Получаем объект администратора
            self.stdout.write(self.style.SUCCESS(f"Найден администратор: {admin_user.username}"))
        except Page.DoesNotExist:
            self.stdout.write(self.style.ERROR("Ошибка: Не найдена родительская страница со слагом 'page'. Создайте ее в админке, как описано в инструкции."))
            return
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR("Ошибка: Не найден пользователь с ID=1. Убедитесь, что администратор создан."))
            return

        # Для сопоставления старых ID с новыми объектами
        legacy_page_map = {}
        legacy_category_map = {}
        legacy_comment_map = {}

        with connection.cursor() as cursor:
            # --- 1. Импорт категорий --- (Без изменений)
            self.stdout.write("1. Импорт категорий...")
            cursor.execute("SELECT * FROM legacy.mso_category")
            categories_data = dictfetchall(cursor)

            legacy_category_map = {}

            for cat_data in categories_data:
                new_cat, created = Category.objects.update_or_create(
                    slug=cat_data['category_slug'] or slugify(cat_data['category_name'], allow_unicode=True),
                    defaults={
                        'name': cat_data['category_name'],
                        'parent': None
                    }
                )
                if created:
                    self.stdout.write(f"  - Создана категория: {new_cat.name}")
                legacy_category_map[cat_data['category_id']] = new_cat

            for cat_data in categories_data:
                if cat_data['category_id_parent'] != 0:
                    child_cat = legacy_category_map.get(cat_data['category_id'])
                    parent_cat = legacy_category_map.get(cat_data['category_id_parent'])
                    if child_cat and parent_cat:
                        Category.objects.filter(pk=child_cat.pk).update(parent=parent_cat)

            self.stdout.write("  - Перестроение дерева категорий...")
            Category.objects.rebuild()
            self.stdout.write(self.style.SUCCESS("Категории импортированы."))

            # --- 2. Импорт записей блога --- (Без изменений, кроме owner=author на owner=admin_user)
            self.stdout.write("\n2. Импорт записей блога...")
            cursor.execute("SELECT * FROM legacy.mso_page WHERE page_status = 'publish'")
            pages_data = dictfetchall(cursor)

            for page_data in pages_data:
                old_page_id = page_data['page_id']
                self.stdout.write(f"  - Обработка: '{page_data['page_title']}'")

                final_slug = page_data['page_slug'] or slugify(page_data['page_title'], allow_unicode=True)

                if BlogPage.objects.filter(slug=final_slug, path__startswith=parent_page.path).exists():
                    self.stdout.write(self.style.WARNING(f"    ! Страница со слагом '{final_slug}' уже существует. Пропускаем."))
                    continue

                new_page = BlogPage(
                    title=page_data['page_title'],
                    slug=final_slug,
                    body=page_data['page_content'] or "",
                    owner=admin_user,  # <-- Используем admin_user здесь
                    date_published=page_data['page_date_publish'],
                    first_published_at=page_data['page_date_publish'],
                    last_published_at=page_data['page_last_modified'],
                )

                parent_page.add_child(instance=new_page)
                legacy_page_map[old_page_id] = new_page

                cursor.execute(
                    "SELECT meta_key, meta_value FROM legacy.mso_meta WHERE meta_table = 'page' AND meta_id_obj = %s",
                    [old_page_id]
                )
                meta_data = dictfetchall(cursor)

                for meta in meta_data:
                    if meta['meta_key'] == 'tags' and meta['meta_value']:
                        tags = [tag.strip() for tag in meta['meta_value'].split(',') if tag.strip()]
                        new_page.tags.add(*tags)

                    if meta['meta_key'] == 'image_for_page' and meta['meta_value']:
                        img_url = meta['meta_value']
                        if 'uploads/' in img_url:
                            path_part = urlparse(img_url).path
                            relative_path = path_part[path_part.find('uploads/'):]
                            full_path = os.path.join(settings.MEDIA_ROOT, relative_path)

                            if os.path.exists(full_path):
                                try:
                                    image = CustomImage.objects.get(file=relative_path)
                                except CustomImage.DoesNotExist:
                                    with open(full_path, 'rb') as f:
                                        image = CustomImage(title=os.path.basename(relative_path), file=File(f, name=relative_path))
                                        image.save()
                                new_page.main_image = image
                            else:
                                self.stdout.write(self.style.WARNING(f"    ! Файл не найден: {full_path}"))

                cursor.execute("SELECT category_id FROM legacy.mso_cat2obj WHERE page_id = %s", [old_page_id])
                for cat_link in cursor.fetchall():
                    cat_id = cat_link[0]
                    if cat_id in legacy_category_map:
                        new_page.categories.add(legacy_category_map[cat_id])

                revision = new_page.save_revision()
                revision.publish()

            self.stdout.write(self.style.SUCCESS("Записи блога импортированы."))

            # --- 3. Импорт комментариев --- (ИЗМЕНЕНИЯ ЗДЕСЬ)
            self.stdout.write("\n3. Импорт комментариев...")
            # Сначала загрузим всех пользователей с никами для быстрого доступа
            cursor.execute("SELECT comusers_id, comusers_nik FROM legacy.mso_comusers")
            comusers_map = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT * FROM legacy.mso_comments ORDER BY comments_parent_id ASC")
            comments_data = dictfetchall(cursor)

            # Первый проход: создаем все комментарии
            for comm_data in comments_data:
                old_comm_id = comm_data['comments_id']
                page_id = comm_data['comments_page_id']
                comments_users_id = comm_data['comments_users_id']  # ID пользователя из mso_comments
                comments_comusers_id = comm_data['comments_comusers_id']  # ID пользователя из mso_comusers

                # Пропускаем комментарии к страницам, которые не были импортированы
                if page_id not in legacy_page_map:
                    self.stdout.write(self.style.WARNING(f"  - Пропущен комментарий (ID {old_comm_id}): Страница (ID {page_id}) не импортирована."))
                    continue

                # Определяем имя автора и пользователя Wagtail
                author_name = comm_data['comments_author_name']
                comment_user = None  # По умолчанию пользователь не установлен

                if comments_users_id == 1:
                    # Если comments_users_id = 1, это администратор/автор блога
                    comment_user = admin_user
                    # Имя автора может быть пустым, если это автор. Можем использовать его имя пользователя Wagtail.
                    if not author_name:
                        author_name = admin_user.username
                elif not author_name and comments_comusers_id != 0:
                    # Если comments_author_name пустое, и comments_comusers_id не 0,
                    # берем comusers_nik из mso_comusers
                    author_name = comusers_map.get(comments_comusers_id, "Анонимный пользователь")
                elif not author_name:
                    # Если ни author_name, ни comusers_nik не заполнены
                    author_name = "Гость"

                # Проверяем, существует ли уже комментарий с таким ID в новой БД (если вы его мигрировали ранее)
                # Это важно, если вы перезапускаете скрипт и комментарии уже есть.
                # Если вы уверены, что запускаете миграцию на чистую базу,
                # или если у вас есть legacy_id в вашей Comment модели, это можно упростить.
                # Пока что полагаемся на уникальность page, content, date_created
                existing_comment = Comment.objects.filter(
                    page=legacy_page_map[page_id],
                    content=comm_data['comments_content'] or "",  # Используйте content
                    date_created=comm_data['comments_date'],
                ).first()

                if existing_comment:
                    self.stdout.write(self.style.WARNING(f"  - Комментарий с таким содержимым и датой уже существует (ID {existing_comment.id}). Обновляем."))
                    new_comm = existing_comment
                    new_comm.author_name = author_name
                    new_comm.user = comment_user  # <-- Устанавливаем пользователя
                else:
                    new_comm = Comment(
                        page=legacy_page_map[page_id],
                        author_name=author_name,
                        content=comm_data['comments_content'] or "",
                        date_created=comm_data['comments_date'],
                        parent=None,  # Установим на втором проходе
                        user=comment_user,  # <-- Устанавливаем пользователя
                    )

                new_comm.save()
                legacy_comment_map[old_comm_id] = new_comm
                self.stdout.write(f"  - Создан/Обновлен комментарий (ID {new_comm.id}) от '{author_name}'")

            # Второй проход: устанавливаем родительские связи
            self.stdout.write("  - Установка родительских связей для комментариев...")
            for comm_data in comments_data:
                if comm_data['comments_parent_id'] != 0:
                    child_comm = legacy_comment_map.get(comm_data['comments_id'])
                    parent_comm = legacy_comment_map.get(comm_data['comments_parent_id'])
                    if child_comm and parent_comm and child_comm.parent != parent_comm:  # Избегаем лишних сохранений
                        child_comm.parent = parent_comm
                        child_comm.save()
                        self.stdout.write(f"    - Установлена родительская связь для комментария ID {child_comm.id}")

            # Если вы используете MPTT, рекомендуется перестроить дерево после установки родителей
            self.stdout.write("  - Перестроение дерева комментариев MPTT...")
            Comment.objects.rebuild()
            self.stdout.write(self.style.SUCCESS("Комментарии импортированы."))

        self.stdout.write(self.style.SUCCESS("\nМиграция успешно завершена!"))
