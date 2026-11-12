"""Public media fallback for shared hosts without a configurable web-server alias.

Prefer serving MEDIA_ROOT directly from OpenResty/Apache. This view is a fallback:
it still consumes a Django worker on cache misses, but sends browser/proxy cache
headers so repeated downloads can be avoided.
"""
from pathlib import PurePosixPath

from django.conf import settings
from django.utils.cache import patch_cache_control
from django.views.static import serve as django_serve

_ACTIVE_CONTENT_SUFFIXES = {".svg", ".html", ".htm", ".xhtml", ".xml", ".js", ".mjs"}


def serve_public_media(request, path):
    response = django_serve(request, path, document_root=settings.MEDIA_ROOT)
    response["X-Content-Type-Options"] = "nosniff"

    # Media URLs are public in this project. Use a conservative one-day TTL;
    # do not mark as immutable because some legacy paths may be replaced.
    if response.status_code in (200, 304):
        patch_cache_control(response, public=True, max_age=86400)

    # Uploaded active content must not execute with the site's origin privileges
    # when opened directly. Raster images are unaffected by this response policy.
    if PurePosixPath(path).suffix.lower() in _ACTIVE_CONTENT_SUFFIXES:
        response["Content-Security-Policy"] = "sandbox; default-src 'none';"
        response["Content-Disposition"] = "attachment"
    return response
