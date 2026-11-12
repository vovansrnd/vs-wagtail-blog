"""Разделение кэшей проекта (Этап 2 аудита).

default    — прикладной кэш: карточки списков, облако тегов, дерево рубрик,
             блоки форума. Эволюционный FileBasedCache: вытеснение допустимо.
security   — защитные маркеры: одноразовые токены контактной формы,
             блокировки перебора логинов. Вытеснение недопустимо до истечения
             TTL, поэтому каталог и лимиты отдельные.
pages      — кэш публичных HTML-страниц (blog.page_cache). Любое содержимое
             этого кэша не содержит персональных данных (см. bypass-правила).
persistent — DatabaseCache: долговременный маркер эпохи инвалидации.
             DatabaseCache требует однократного `manage.py createcachetable`
             на целевой БД (создаёт только таблицу django_cache).
"""
import os

CACHE_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "cache")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.path.join(CACHE_BASE, "app"),
        "TIMEOUT": 60 * 60 * 24 * 30,
        "OPTIONS": {"MAX_ENTRIES": 50000, "CULL_FREQUENCY": 8},
    },
    "security": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.path.join(CACHE_BASE, "security"),
        "TIMEOUT": 60 * 60 * 25,
        "OPTIONS": {"MAX_ENTRIES": 20000, "CULL_FREQUENCY": 16},
        "KEY_PREFIX": "sec",
    },
    "pages": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.path.join(CACHE_BASE, "pages"),
        "TIMEOUT": 600,
        "OPTIONS": {"MAX_ENTRIES": 20000, "CULL_FREQUENCY": 8},
        "KEY_PREFIX": "pg",
    },
    "persistent": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "blog_persistent_cache",
        "TIMEOUT": None,
        # Д56: здесь хранятся и used-маркеры математической задачи
        # (TTL 30 мин, атомарное add через PK), и долговременный маркер
        # эпохи page-cache (TIMEOUT None). MAX_ENTRIES поднят, чтобы
        # вытеснение (_cull) не удаляло живые used-маркеры волны регистраций
        # и строку эпохи (её удаление безопасно, но глушит page-cache).
        "OPTIONS": {"MAX_ENTRIES": 20000, "CULL_FREQUENCY": 8},
    },
}
