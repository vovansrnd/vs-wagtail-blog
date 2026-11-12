# vsproject/api_images.py
# Загрузка изображений для publisher'а через v3 API.
#
# Работает на ГОЛОМ Wagtail и с расширенными моделями:
# - если модель изображения имеет поля year/slug (плагин
#   wagtail-image-directories) и метод get_or_create_collection() —
#   используется схема директорий year/slug;
# - на голом Wagtail — обычная коллекция "year/slug" (нативная
#   иерархия коллекций Wagtail, treebeard).
#
# Роутер регистрируется в vsproject/api.py ДО обращения к api.urls.
from ninja import File, Form, Router
from ninja.files import UploadedFile

import os
import warnings
from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.utils.text import slugify

from wagtail.api.v3.auth import BearerTokenAuth
from wagtail.images import get_image_model

Image = get_image_model()

# Обнаружение схемы директорий (не хардкодим конкретный плагин)
_HAS_DIRECTORY_FIELDS = all(
    any(f.name == name for f in Image._meta.get_fields())
    for name in ("year", "slug")
)
_HAS_GET_OR_CREATE_COLLECTION = hasattr(Image, "get_or_create_collection")
HAS_DIRECTORY_SCHEME = _HAS_DIRECTORY_FIELDS and _HAS_GET_OR_CREATE_COLLECTION


class _PublisherCollectionPermissionDenied(Exception):
    pass


def _clean_directory_part(value, *, fallback="", max_length=255):
    cleaned = slugify(str(value or "").strip(), allow_unicode=True)
    return cleaned[:max_length] or fallback


def _validate_uploaded_image(upload):
    """Validate size, extension, decoded format and pixel count before saving."""
    try:
        from PIL import Image as PILImage
        from PIL import UnidentifiedImageError
    except ImportError:
        return "Image validation is unavailable on this server."

    max_size = int(getattr(settings, "PUBLISHER_IMAGE_MAX_UPLOAD_SIZE", 50 * 1024 * 1024))
    max_pixels = int(getattr(settings, "PUBLISHER_IMAGE_MAX_PIXELS", 89_478_485))
    size = getattr(upload, "size", None)
    if size is None or size <= 0:
        return "The uploaded image is empty or has no known size."
    if size > max_size:
        return f"Image exceeds the {max_size // (1024 * 1024)} MiB upload limit."

    name = getattr(upload, "name", "") or ""
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    allowed_extensions = set(getattr(
        settings, "WAGTAILIMAGES_EXTENSIONS",
        ("bmp", "gif", "jpeg", "jpg", "png", "webp"),
    ))
    if ext not in allowed_extensions:
        return "This image file extension is not allowed."

    expected_format = PILImage.registered_extensions().get("." + ext)
    if not expected_format:
        return "This image file extension is not supported by the image decoder."
    file_obj = getattr(upload, "file", upload)
    try:
        file_obj.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            with PILImage.open(file_obj) as image:
                if expected_format and image.format != expected_format:
                    return "The file extension does not match the image content."
                if image.width <= 0 or image.height <= 0:
                    return "The image dimensions are invalid."
                if max_pixels > 0 and image.width * image.height > max_pixels:
                    return "The image exceeds the configured pixel limit."
                image.verify()
    except (UnidentifiedImageError, OSError, ValueError, PILImage.DecompressionBombError,
            PILImage.DecompressionBombWarning):
        return "The uploaded file is not a valid supported image."
    finally:
        try:
            file_obj.seek(0)
        except Exception:
            pass
    return None


def _image_add_policy():
    from wagtail.permissions import policy_registry
    return policy_registry.get_by_type(Image)



def _native_collection(year, slug):
    """Находит/создаёт коллекцию путём year/slug на голом Wagtail."""
    from wagtail.models import Collection

    root = Collection.get_first_root_node()
    node = root
    for name in [str(year)] + ([str(slug)] if slug else []):
        child = node.get_children().filter(name=name).first()
        if child is None:
            child = node.add_child(name=name)
        node = child
    return node


def _image_to_dict(img, directory_scheme):
    data = {
        "id": img.id,
        "title": img.title,
        "url": img.file.url,
        "download_url": img.file.url,
        "width": getattr(img, "width", None),
        "height": getattr(img, "height", None),
        "file_size": img.get_file_size() if hasattr(img, "get_file_size") else None,
        "directory_scheme": "year/slug" if directory_scheme else "collections",
    }
    if directory_scheme:
        data["year"] = img.year
        data["slug"] = img.slug
    else:
        data["collection_id"] = img.collection_id
    return data


router = Router(tags=["publisher-images"])


@router.post(
    "/",
    operation_id="publisher_images_upload",
    summary="Загрузить изображение (директория year/slug или коллекция)",
    auth=BearerTokenAuth(),
)
def upload_image(
    request,
    title: str = Form(...),
    file: UploadedFile = File(...),
    year: str = Form("misc"),
    slug: str = Form(""),
):
    from django.http import JsonResponse

    validation_error = _validate_uploaded_image(file)
    if validation_error:
        return JsonResponse({"detail": validation_error}, status=400)

    # Normalize path components before they are used in collection names or
    # upload_to. A slash, backslash or dot segment must never become a path part.
    clean_year = _clean_directory_part(year, fallback="misc", max_length=50)
    clean_slug = _clean_directory_part(slug, fallback="", max_length=255)

    try:
        with transaction.atomic():
            if HAS_DIRECTORY_SCHEME:
                probe = Image(title="__permission_check__", year=clean_year, slug=clean_slug)
                probe.collection = probe.get_or_create_collection()
            else:
                target_collection = _native_collection(clean_year, clean_slug)
                probe = Image(title="__permission_check__", collection=target_collection)

            policy = _image_add_policy()
            if not policy.user_has_permission_for_instance(request.user, "add", probe):
                raise _PublisherCollectionPermissionDenied

            img = Image(title=(title or file.name).strip()[:255])
            img.file = file
            # Attribute the upload to the authenticated API-token owner, as the
            # Wagtail admin upload flow does. Some custom image models may omit it.
            try:
                Image._meta.get_field("uploaded_by_user")
            except FieldDoesNotExist:
                pass
            else:
                if getattr(request.user, "is_authenticated", False):
                    img.uploaded_by_user = request.user
            if HAS_DIRECTORY_SCHEME:
                img.year = clean_year
                img.slug = clean_slug
                img.collection = probe.collection
            else:
                img.collection = probe.collection
            img.save()
    except _PublisherCollectionPermissionDenied:
        return JsonResponse(
            {"detail": "Недостаточно прав для загрузки в эту коллекцию."}, status=403
        )
    except Exception:
        # Let Django's exception handlers log and report unexpected server errors;
        # do not disguise storage/DB failures as client-side 400 responses.
        import logging
        logging.getLogger(__name__).exception("Publisher image upload failed")
        raise

    return _image_to_dict(img, HAS_DIRECTORY_SCHEME)
