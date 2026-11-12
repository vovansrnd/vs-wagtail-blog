from pathlib import Path

from django.conf import settings
from django.db import models
from django.templatetags.static import static


def user_avatar_upload_to(instance, filename):
    ext = Path(filename).suffix.lower() or ".jpg"
    return f"avatars/users/{instance.user_id}/avatar{ext}"


class UserProfile(models.Model):
    SOURCE_UPLOAD = "upload"
    SOURCE_GITHUB = "github"
    SOURCE_YANDEX = "yandex"
    SOURCE_VK = "vk"
    SOURCE_GRAVATAR = "gravatar"

    SOURCE_CHOICES = [
        (SOURCE_UPLOAD, "Загружен вручную"),
        (SOURCE_GITHUB, "GitHub"),
        (SOURCE_YANDEX, "Yandex"),
        (SOURCE_VK, "VK"),
        (SOURCE_GRAVATAR, "Gravatar"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="Пользователь",
    )
    avatar = models.ImageField(
        upload_to=user_avatar_upload_to,
        blank=True,
        null=True,
        verbose_name="Локальный аватар",
    )
    avatar_source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        blank=True,
        default="",
        verbose_name="Источник аватара",
    )
    avatar_checked_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Когда в последний раз проверяли внешний аватар",
    )
    avatar_fetch_error = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Ошибка загрузки аватара",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_avatar_url(self):
        if self.avatar:
            return self.avatar.url
        return static("img/default_avatar.png")

    def __str__(self):
        return f"Профиль {self.user}"
