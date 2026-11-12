"""Кэш публичных HTML-страниц (Этап 1 аудита).

Правила хранения сформулированы так, чтобы в общем кэше физически не могло
оказаться персонального содержимого (см. тесты blog/tests_cache.py):

- только GET и только анонимные запросы (сессионная cookie = bypass);
- только адреса Wagtail-страниц: пути служебных разделов исключены целиком;
- только ответы 200 text/html без Set-Cookie и без Vary: Cookie;
- запросы с параметрами вне белого списка (page) — bypass;
- запросы с cookie pending-сообщений — bypass.

Инвалидация: ключ включает эпоху из persistent-кэша; любое событие
публикации/комментария/форума меняет эпоху (blog.signals), после чего все
страницы пересоздаются из БД. Компромисс осознанный: вместо точечной
инвалидации сбрасывается весь page-кэш (дешевле, чем риск устаревания).
"""
from django.core.cache import caches
from django.utils.translation import get_language_from_request

from .cache_utils import get_blog_cache_epoch

PAGES = caches["pages"]
PAGE_TTL = 600  # секунд; браузерам отдаётся короткий max-age, прокси — s-maxage

# Пути, которые никогда не кэшируются (служебные/персональные разделы)
EXCLUDED_PREFIXES = (
    "/admin/", "/django-admin/", "/accounts/", "/comments/", "/forum/",
    "/reader/", "/games/", "/personal/", "/search/", "/api/", "/media/",
    "/static/", "/documents/", "/sitemap", "/feed", "/robots.txt",
    "/unsubscribe", "/contact/", "/archive/",
)

# Параметры запроса, разрешённые в ключе (пагинация). Всё остальное — bypass.
ALLOWED_QUERY = {"page"}

# Analytics/click identifiers do not change the rendered page. Ignore them in
# the cache key so a single article does not lose cache hits for every campaign
# URL. Other unknown query parameters remain a strict cache bypass.
TRACKING_QUERY_PARAMS = {
    "yclid", "ysclid", "fbclid", "gclid", "gbraid", "wbraid",
    "_openstat", "mc_cid", "mc_eid",
}
TRACKING_QUERY_PREFIXES = ("utm_",)


def _is_tracking_parameter(name):
    normalized = name.lower()
    return normalized in TRACKING_QUERY_PARAMS or any(
        normalized.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES
    )

# Cookie, указывающие на пользовательское состояние запроса
STATE_COOKIES = ("sessionid", "messages")


def _cache_key(request):
    if request.method != "GET":
        return None
    path = request.path
    if any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return None
    # Анонимность: наличие сессионной cookie = персональный запрос
    for name in STATE_COOKIES:
        if name in request.COOKIES:
            return None
    # Параметры: только белые списки, прочие — bypass
    query = request.GET
    unknown = {
        name for name in query
        if name not in ALLOWED_QUERY and not _is_tracking_parameter(name)
    }
    if unknown:
        return None
    page_values = query.getlist("page")
    if len(page_values) > 1:
        return None
    if page_values:
        raw_page = page_values[0]
        # ASCII decimal only; bound the cache namespace against arbitrary huge
        # query values and normalize leading zeroes (page=01 == page=1).
        if not raw_page or not raw_page.isascii() or not raw_page.isdecimal():
            return None
        # Avoid converting attacker-controlled, arbitrarily long decimal
        # strings (Python itself also caps huge int conversions).
        if len(raw_page) > 32:
            return None
        normalized_page = raw_page.lstrip("0") or "0"
        if len(normalized_page) > 6:
            return None
        page_number = int(normalized_page)
        if not 1 <= page_number <= 100000:
            return None
        page = str(page_number)
    else:
        page = "1"

    # The raw Accept-Language header creates many equivalent cache keys.
    # Django resolves it against configured languages (and language cookie).
    language = get_language_from_request(request, check_path=False)
    return "|".join([
        get_blog_cache_epoch(), request.get_host().lower(), path, page, language or "default"
    ])


def _is_storable(response):
    if response.status_code != 200:
        return False
    ctype = response.headers.get("Content-Type", "")
    if "text/html" not in ctype:
        return False
    if response.headers.get("Set-Cookie"):
        return False
    vary = response.headers.get("Vary", "")
    if "cookie" in vary.lower():
        return False
    cc = response.headers.get("Cache-Control", "")
    if any(flag in cc for flag in ("private", "no-store", "no-cache")):
        return False
    return True


class PageCacheMiddleware:
    """Отдаёт анонимные GET из pages-кэша и сохраняет свежие ответы.

    Стоит в MIDDLEWARE первым после SecurityMiddleware: попадание в кэш
    отрабатывает до сессий и аутентификации (быстро и без доступа к
    персональному состоянию); сохранение выполняется в фазе ответа, когда
    итоговые Set-Cookie/Vary уже известны — ответ с ними не сохраняется.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        key = _cache_key(request)
        if key is not None:
            cached = PAGES.get(key)
            if cached is not None:
                response = cached
                response["X-Page-Cache"] = "hit"
                return response
            request._page_cache_key = key
        response = self.get_response(request)
        key = getattr(request, "_page_cache_key", None)
        if key is not None and _is_storable(response):
            PAGES.set(key, response, PAGE_TTL)
            response["X-Page-Cache"] = "miss"
        return response
