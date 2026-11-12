# vsproject/api.py
# Регистрирует кастомные роутеры (images) на инстансе Wagtail v3 API.
# ВАЖНО: этот модуль должен быть импортирован ДО обращения к
# wagtail.api.v3.urls (Django Ninja запрещает add_router после доступа
# к api.urls). urls.py импортирует его первой строкой.
from wagtail.api.v3.api import api  # noqa: F401  (сам инстанс API)

from .api_images import router as images_router  # noqa: F401
from .api_meta import router as meta_router  # noqa: F401

api.add_router("/publisher-images/", images_router)
api.add_router("/publisher-meta/", meta_router)
