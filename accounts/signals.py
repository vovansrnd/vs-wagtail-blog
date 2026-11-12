# accounts/signals.py — DB-only сигналы: профили и уведомления админов.
# Сетевая загрузка аватаров при входе вынесена в avatar_signals.py и
# подключается только при AVATAR_AUTO_FETCH_ON_LOGIN=True (см. apps.py).

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import UserProfile

User = get_user_model()


def _admin_recipients():
    recipients = [email for _, email in getattr(settings, "ADMINS", []) if email]
    return recipients or [settings.DEFAULT_FROM_EMAIL]


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=User)
def notify_admins_on_new_user(sender, instance, created, **kwargs):
    """
    Уведомление админу о новой регистрации.
    Срабатывает и на обычную регистрацию, и на первую social-регистрацию.
    """
    if not created:
        return

    if not getattr(settings, "USERS_NOTIFY_ADMINS", True):
        return

    username = instance.username or "(без username)"
    full_name = instance.get_full_name() or "Не указано"
    email = instance.email or "Не указан"

    subject = (
        f"[{getattr(settings, 'SITE_NAME', 'Сайт')}] Новый пользователь: {username}"
    )
    body = (
        "На сайте зарегистрирован новый пользователь.\n\n"
        f"Username: {username}\n"
        f"Полное имя: {full_name}\n"
        f"Email: {email}\n"
        f"ID: {instance.id}\n"
    )

    recipients = _admin_recipients()

    transaction.on_commit(
        lambda: send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=True,
        )
    )


@receiver(pre_save, sender=UserProfile)
def delete_old_avatar_on_change(sender, instance, **kwargs):
    if not instance.pk:
        return

    try:
        old = UserProfile.objects.get(pk=instance.pk)
    except UserProfile.DoesNotExist:
        return

    if old.avatar and old.avatar != instance.avatar:
        old.avatar.delete(save=False)


@receiver(post_delete, sender=UserProfile)
def delete_avatar_file_on_profile_delete(sender, instance, **kwargs):
    if instance.avatar:
        instance.avatar.delete(save=False)
