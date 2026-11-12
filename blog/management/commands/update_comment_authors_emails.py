# blog/management/commands/update_comment_authors_emails.py
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.contrib.auth import get_user_model
from blog.models import Comment  # Import your Comment model
from wagtail.models import Page
from django.utils import timezone
import pytz  # Import pytz for timezone awareness


class Command(BaseCommand):
    help = 'Updates existing comments in Wagtail with user, email (nullable), and new boolean fields from legacy data.'

    def handle(self, *args, **options):
        User = get_user_model()

        wagtail_admin_user_id = 1
        try:
            admin_user = User.objects.get(id=wagtail_admin_user_id)
            self.stdout.write(self.style.SUCCESS(f"Найден администратор Wagtail: {admin_user.username} (ID: {admin_user.id})"))
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Ошибка: Администратор Wagtail с ID {wagtail_admin_user_id} не найден. Проверьте auth_user таблицу."))
            return

        self.stdout.write(self.style.SUCCESS('Начинаем обновление данных комментариев...'))

        # Step 1: Получаем карту соответствий старых mso_page.page_id и новых wagtailcore_page.id
        page_id_map_old_to_new = {}
        with connection.cursor() as cursor:
            cursor.execute("""
                           SELECT wp.id      as wagtail_page_id,
                                  mp.page_id as mso_page_id
                           FROM wagtailcore_page wp
                                    JOIN blog_blogpage bp ON wp.id = bp.page_ptr_id
                                    JOIN legacy.mso_page mp ON wp.slug = mp.page_slug;
                           """)
            for row in cursor.fetchall():
                wagtail_page_id, mso_page_id = row
                page_id_map_old_to_new[mso_page_id] = wagtail_page_id

        self.stdout.write(self.style.SUCCESS(f"Сопоставлено {len(page_id_map_old_to_new)} старых ID страниц с новыми Wagtail page IDs."))

        # Step 2: Загружаем данные пользователей из legacy.mso_comusers для быстрого доступа
        comusers_data = {}
        with connection.cursor() as cursor:
            cursor.execute("SELECT comusers_id, comusers_nik, comusers_email FROM legacy.mso_comusers;")
            for row in cursor.fetchall():
                comusers_data[row[0]] = {'nik': row[1], 'email': row[2]}
        self.stdout.write(self.style.SUCCESS(f"Загружено {len(comusers_data)} записей comusers из legacy DB."))

        # Step 3: Получаем все данные комментариев из legacy.mso_comments
        legacy_comments_data = []
        with connection.cursor() as cursor:
            cursor.execute("""
                           SELECT mc.comments_id,
                                  mc.comments_page_id,
                                  mc.comments_content,
                                  mc.comments_date,
                                  mc.comments_author_name,
                                  mc.comments_users_id,   -- User ID from legacy auth system
                                  mc.comments_comusers_id -- Comuser ID from legacy comment system
                           FROM legacy.mso_comments mc;
                           """)
            legacy_comments_data = cursor.fetchall()

        self.stdout.write(self.style.SUCCESS(f"Найдено {len(legacy_comments_data)} комментариев в legacy DB для обработки."))

        comments_updated_count = 0

        target_timezone = pytz.timezone(settings.TIME_ZONE)

        with transaction.atomic():
            for comment_row in legacy_comments_data:
                (legacy_comment_id, legacy_page_id, legacy_content,
                 legacy_date_naive, legacy_author_name_orig,
                 legacy_comments_users_id, legacy_comments_comusers_id) = comment_row

                # Make datetime timezone-aware
                if legacy_date_naive:
                    legacy_date_aware = target_timezone.localize(legacy_date_naive)
                else:
                    legacy_date_aware = timezone.now()

                new_wagtail_page_id = page_id_map_old_to_new.get(legacy_page_id)

                if not new_wagtail_page_id:
                    self.stderr.write(self.style.WARNING(f"Пропущено: Не найдена новая Wagtail Page для старой Page ID {legacy_page_id} (legacy comment ID: {legacy_comment_id})."))
                    continue

                comment_user = None
                comment_author_email = None  # Default to None, as it's now nullable

                # Assign user and email based on legacy system's user IDs
                if legacy_comments_users_id == 1:
                    comment_user = admin_user
                    comment_author_email = admin_user.email
                elif legacy_comments_comusers_id != 0:
                    comuser_info = comusers_data.get(legacy_comments_comusers_id)
                    if comuser_info:
                        # Use email if available, otherwise None
                        comment_author_email = comuser_info['email'] if comuser_info['email'] else None

                # Determine author_name if original is empty
                actual_author_name_in_wagtail = legacy_author_name_orig
                if not actual_author_name_in_wagtail:
                    if comment_user:
                        actual_author_name_in_wagtail = comment_user.username
                    elif legacy_comments_comusers_id != 0:
                        comuser_info = comusers_data.get(legacy_comments_comusers_id)
                        if comuser_info and comuser_info['nik']:
                            actual_author_name_in_wagtail = comuser_info['nik']
                    else:
                        actual_author_name_in_wagtail = "Аноним"

                try:
                    # Try to find the existing comment in Wagtail DB using its unique identifiers
                    comment_to_update = Comment.objects.get(
                        page_id=new_wagtail_page_id,
                        content=legacy_content,
                        date_created=legacy_date_naive  # Keep naive for GET for consistency with existing data
                    )

                    updated_fields = []

                    # Update user if not already set or if a more specific user is found
                    if comment_user and (comment_to_update.user is None or comment_to_update.user != comment_user):
                        comment_to_update.user = comment_user
                        updated_fields.append('user')

                    # Update author_name if different or empty
                    if comment_to_update.author_name != actual_author_name_in_wagtail:
                        comment_to_update.author_name = actual_author_name_in_wagtail
                        updated_fields.append('author_name')

                    # Update author_email if different or if it was None and we found one
                    if comment_author_email is not None and comment_to_update.author_email != comment_author_email:
                        comment_to_update.author_email = comment_author_email
                        updated_fields.append('author_email')
                    elif comment_author_email is None and comment_to_update.author_email is not None:
                        # If legacy has no email, but current has one, keep current.
                        # If legacy has no email and current has no email, do nothing.
                        pass  # No update if the new value is None and old value exists

                    # Update date_created to be timezone-aware (if it was naive)
                    if timezone.is_naive(comment_to_update.date_created):
                        comment_to_update.date_created = target_timezone.localize(comment_to_update.date_created)
                        updated_fields.append('date_created')

                    # Set new boolean fields for historical data
                    if not comment_to_update.notify_on_reply:  # Set default for historical
                        comment_to_update.notify_on_reply = False
                        updated_fields.append('notify_on_reply')
                    if not comment_to_update.is_approved:  # Historical comments are approved by default for this purpose
                        comment_to_update.is_approved = True  # Changed from False to True for historical
                        updated_fields.append('is_approved')

                    # NOTE: is_historical field is no longer in your model, so it's removed from the script.

                    if updated_fields:
                        comment_to_update.save(update_fields=updated_fields)
                        comments_updated_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(f"Обновлен комментарий ID {comment_to_update.id} (legacy {legacy_comment_id}). Обновлены поля: {', '.join(updated_fields)}."))
                    else:
                        self.stdout.write(self.style.WARNING(f"Комментарий ID {comment_to_update.id} (legacy {legacy_comment_id}) не требует обновлений. Пропущен."))

                except Comment.DoesNotExist:
                    self.stderr.write(self.style.ERROR(
                        f"Ошибка: Не найден соответствующий комментарий в Wagtail DB для legacy comment ID {legacy_comment_id} (page ID {new_wagtail_page_id}). Убедитесь, что `content` и `date_created` уникальны для поиска."))
                except Comment.MultipleObjectsReturned:
                    self.stderr.write(self.style.ERROR(
                        f"Ошибка: Найдено несколько комментариев в Wagtail DB для legacy comment ID {legacy_comment_id} (page ID {new_wagtail_page_id}). Требуется ручное вмешательство, проверьте уникальность полей (content, date_created)."))
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"Неизвестная ошибка при обработке legacy комментария ID {legacy_comment_id}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"\nОбновление завершено. Всего обновлено {comments_updated_count} комментариев."))
