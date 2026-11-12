# blog/management/commands/update_comment_authors.py

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.contrib.auth import get_user_model
from blog.models import Comment  # Импортируем вашу модель Comment
from wagtail.models import Page  # Для доступа к wagtailcore_page (нужно для сопоставления ID)
#from blog.models import BlogPage # Может быть не нужен, если вы сопоставляете через wagtailcore_page


class Command(BaseCommand):
    help = 'Updates the user_id for existing comments in Wagtail based on legacy comments_users_id = 1.'

    def handle(self, *args, **options):
        User = get_user_model()

        # ID администратора Wagtail, к которому будут привязаны комментарии
        # Убедитесь, что этот ID соответствует ID вашего админа в auth_user
        wagtail_admin_user_id = 1
        try:
            admin_user = User.objects.get(id=wagtail_admin_user_id)
            self.stdout.write(self.style.SUCCESS(f"Найден администратор Wagtail: {admin_user.username} (ID: {admin_user.id})"))
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Ошибка: Администратор Wagtail с ID {wagtail_admin_user_id} не найден. Проверьте auth_user таблицу."))
            return

        self.stdout.write(self.style.SUCCESS('Начинаем обновление пользователей комментариев...'))

        # Шаг 1: Получаем карту соответствий старых mso_page.page_id и новых wagtailcore_page.id
        # Это критично для сопоставления комментариев со статьями.
        page_id_map_old_to_new = {}
        with connection.cursor() as cursor:
            # Получаем все текущие BlogPage в Wagtail и их соответствующие page_ptr_id (который является ID в wagtailcore_page)
            # и slug для сопоставления со старой БД (если вы использовали slug для сопоставления статей)
            cursor.execute("""
                SELECT 
                    wp.id as wagtail_page_id, 
                    mp.page_id as mso_page_id -- Получаем старый ID страницы
                FROM wagtailcore_page wp
                JOIN blog_blogpage bp ON wp.id = bp.page_ptr_id -- Предполагаем, что blog_blogpage ссылается на wagtailcore_page
                JOIN legacy.mso_page mp ON wp.slug = mp.page_slug; -- Сопоставляем по slug
            """)
            for row in cursor.fetchall():
                wagtail_page_id, mso_page_id = row
                page_id_map_old_to_new[mso_page_id] = wagtail_page_id # Ключ - старый ID, значение - новый ID

        self.stdout.write(self.style.SUCCESS(f"Карта соответствия старых и новых ID страниц: {page_id_map_old_to_new}"))

        # Шаг 2: Получаем комментарии из старой БД, которые были от администратора
        legacy_comments_data = []
        with connection.cursor() as cursor:
            # ВЫБИРАЕМ ТОЛЬКО ТЕ КОММЕНТАРИИ, ГДЕ comments_users_id = 1
            cursor.execute("""
                SELECT 
                    mc.comments_id,
                    mc.comments_page_id,
                    mc.comments_content,
                    mc.comments_date,
                    mc.comments_author_name,
                    mcu.comusers_nik -- На случай, если author_name пустое
                FROM legacy.mso_comments mc
                LEFT JOIN legacy.mso_comusers mcu ON mc.comments_comusers_id = mcu.comusers_id
                WHERE mc.comments_users_id = 1; 
            """)
            legacy_comments_data = cursor.fetchall()

        self.stdout.write(self.style.SUCCESS(f"Найдено {len(legacy_comments_data)} комментариев в старой БД, оставленных администратором."))

        comments_updated_count = 0

        with transaction.atomic(): # Используем транзакцию для безопасности
            for comment_row in legacy_comments_data:
                (legacy_comment_id, legacy_page_id, legacy_content,
                 legacy_date, legacy_author_name_orig, comusers_nik) = comment_row

                # Определяем ID страницы в новой Wagtail DB
                new_wagtail_page_id = page_id_map_old_to_new.get(legacy_page_id)

                if not new_wagtail_page_id:
                    self.stderr.write(self.style.WARNING(f"Пропущено: Не найдена новая Wagtail Page для старой Page ID {legacy_page_id} (legacy comment ID: {legacy_comment_id})."))
                    continue

                # Определяем имя автора, которое должно быть в новой таблице
                # Это важно для сопоставления, если `content` не уникален.
                # Оно должно соответствовать тому, что уже записано в `blog_comment.author_name`
                actual_author_name_in_wagtail = legacy_author_name_orig
                if not actual_author_name_in_wagtail and comusers_nik:
                    actual_author_name_in_wagtail = comusers_nik
                elif not actual_author_name_in_wagtail: # Если оба пустые
                    actual_author_name_in_wagtail = admin_user.username # Предполагаем, что имя админа уже использовалось

                try:
                    # Ищем существующий комментарий в Wagtail DB
                    # Используем комбинацию полей, которые были перенесены и являются уникальными
                    # Предполагаем, что content, date_created и page_id вместе уникальны для комментария
                    comment_to_update = Comment.objects.get(
                        page_id=new_wagtail_page_id,
                        content=legacy_content,
                        date_created=legacy_date,
                        # Если author_name также был уникален для данной статьи+даты+контента, добавьте:
                        # author_name=actual_author_name_in_wagtail,
                        # Если у вас есть legacy_id в Comment модели, используйте его:
                        # legacy_id=legacy_comment_id,
                    )

                    # Обновляем поле user, только если оно еще не установлено
                    if comment_to_update.user is None:
                        comment_to_update.user = admin_user
                        comment_to_update.save(update_fields=['user'])
                        comments_updated_count += 1
                        self.stdout.write(self.style.SUCCESS(f"Обновлен комментарий ID {comment_to_update.id} (legacy {legacy_comment_id}) на странице (new ID: {new_wagtail_page_id}) пользователем {admin_user.username}."))
                    else:
                        self.stdout.write(self.style.WARNING(f"Комментарий ID {comment_to_update.id} (legacy {legacy_comment_id}) уже привязан к пользователю {comment_to_update.user.username}. Пропущен."))

                except Comment.DoesNotExist:
                    self.stderr.write(self.style.ERROR(f"Ошибка: Не найден соответствующий комментарий в Wagtail DB для legacy comment ID {legacy_comment_id} (page ID {legacy_page_id})."))
                except Comment.MultipleObjectsReturned:
                    self.stderr.write(self.style.ERROR(f"Ошибка: Найдено несколько комментариев в Wagtail DB для legacy comment ID {legacy_comment_id} (page ID {legacy_page_id}). Требуется ручное вмешательство, проверьте уникальность полей."))
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"Неизвестная ошибка при обработке legacy комментария ID {legacy_comment_id}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Обновление завершено. Всего обновлено {comments_updated_count} комментариев."))
