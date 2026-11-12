import hashlib
from io import BytesIO

import requests
from PIL import Image as PILImage, UnidentifiedImageError
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from .models import UserProfile

REQUEST_TIMEOUT = (3, 5)
USER_AGENT = "VsSvetAvatarFetcher/1.0"
MAX_AVATAR_BYTES = 4 * 1024 * 1024
MAX_AVATAR_PIXELS = 20_000_000
_ALLOWED_AVATAR_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
}


def _download_image(url):
    """Download a bounded raster avatar; never store SVG or unverified bytes."""
    response = None
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            stream=True,
        )
        if response.status_code == 404:
            return None, None, "404"
        response.raise_for_status()

        content_type = (
            response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        )
        if content_type not in {item[0] for item in _ALLOWED_AVATAR_FORMATS.values()}:
            return None, None, f"unsupported_image_type:{content_type or 'missing'}"

        declared_size = response.headers.get("Content-Length")
        if declared_size:
            try:
                if int(declared_size) > MAX_AVATAR_BYTES:
                    return None, None, "image_too_large"
            except ValueError:
                return None, None, "invalid_content_length"

        chunks = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            chunks.extend(chunk)
            if len(chunks) > MAX_AVATAR_BYTES:
                return None, None, "image_too_large"

        if not chunks:
            return None, None, "empty_image"

        with PILImage.open(BytesIO(chunks)) as image:
            image_format = image.format
            dimensions = image.size
            if image_format not in _ALLOWED_AVATAR_FORMATS:
                return None, None, f"unsupported_image_format:{image_format}"
            if not dimensions or dimensions[0] <= 0 or dimensions[1] <= 0:
                return None, None, "invalid_image_dimensions"
            if dimensions[0] * dimensions[1] > MAX_AVATAR_PIXELS:
                return None, None, "image_too_many_pixels"
            image.verify()

        expected_type, extension = _ALLOWED_AVATAR_FORMATS[image_format]
        if content_type != expected_type:
            return None, None, "content_type_mismatch"
        return bytes(chunks), extension, None

    except (requests.RequestException, UnidentifiedImageError, PILImage.DecompressionBombError, OSError, ValueError) as exc:
        return None, None, str(exc)
    finally:
        if response is not None:
            response.close()


def _get_github_avatar_url(account):
    return (account.extra_data or {}).get("avatar_url")


def _get_yandex_avatar_url(account):
    data = account.extra_data or {}
    avatar_id = data.get("default_avatar_id")
    if not avatar_id:
        return None
    return f"https://avatars.yandex.net/get-yapic/{avatar_id}/islands-200"


def _get_vk_avatar_url(account):
    data = account.extra_data or {}
    for key in ("photo_max_orig", "photo_200", "photo_100"):
        if data.get(key):
            return data[key]
    return None


def get_social_avatar_candidates(user):
    """
    Возвращает список кандидатов [(source, url), ...]
    в порядке приоритета.
    """
    accounts = list(SocialAccount.objects.filter(user=user))

    provider_priority = ["github", "yandex", "vk"]
    accounts.sort(
        key=lambda a: (
            provider_priority.index(a.provider)
            if a.provider in provider_priority
            else 999
        )
    )

    result = []

    for account in accounts:
        if account.provider == "github":
            url = _get_github_avatar_url(account)
            if url:
                result.append((UserProfile.SOURCE_GITHUB, url))

        elif account.provider == "yandex":
            url = _get_yandex_avatar_url(account)
            if url:
                result.append((UserProfile.SOURCE_YANDEX, url))

        elif account.provider == "vk":
            url = _get_vk_avatar_url(account)
            if url:
                result.append((UserProfile.SOURCE_VK, url))

    return result


def get_gravatar_url(email, size=256):
    if not email:
        return None
    email_hash = hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()
    return f"https://www.gravatar.com/avatar/{email_hash}?d=404&s={size}"


def _save_avatar(profile, content, ext, source):
    if profile.avatar:
        profile.avatar.delete(save=False)

    filename = f"avatar{ext}"
    profile.avatar.save(filename, ContentFile(content), save=False)
    profile.avatar_source = source
    profile.avatar_checked_at = timezone.now()
    profile.avatar_fetch_error = ""
    profile.save()


def fetch_avatar_for_user(user, force=False):
    """
    Скачивает аватар в локальное хранилище:
    1. аватар связанного socialaccount, если доступен;
    2. Gravatar только при AVATAR_FETCH_GRAVATAR=True.

    В обычной конфигурации Gravatar не вызывается и внешняя зависимость
    от gravatar.com отсутствует.

    Возвращает dict со статусом.
    """
    profile, _ = UserProfile.objects.get_or_create(user=user)

    if profile.avatar and not force:
        return {
            "status": "skipped",
            "reason": "local_avatar_exists",
            "source": profile.avatar_source,
        }

    if profile.avatar_checked_at and not force and not profile.avatar:
        return {
            "status": "skipped",
            "reason": "already_checked",
            "source": "",
        }

    # 1. Сначала socialaccount
    for source, url in get_social_avatar_candidates(user):
        content, ext, error = _download_image(url)
        if content:
            _save_avatar(profile, content, ext, source)
            return {
                "status": "downloaded",
                "source": source,
                "url": url,
            }

    # 2. Gravatar is optional. In environments where gravatar.com is blocked,
    # a request would only delay login; the local default avatar remains usable.
    if getattr(settings, "AVATAR_FETCH_GRAVATAR", False):
        gravatar_url = get_gravatar_url(user.email)
        if gravatar_url:
            content, ext, error = _download_image(gravatar_url)
            if content:
                _save_avatar(profile, content, ext, UserProfile.SOURCE_GRAVATAR)
                return {
                    "status": "downloaded",
                    "source": UserProfile.SOURCE_GRAVATAR,
                    "url": gravatar_url,
                }

    # 3. Ничего не нашли
    profile.avatar_checked_at = timezone.now()
    profile.avatar_fetch_error = "avatar_not_found"
    profile.save(
        update_fields=["avatar_checked_at", "avatar_fetch_error", "updated_at"]
    )

    return {
        "status": "not_found",
        "source": "",
    }
