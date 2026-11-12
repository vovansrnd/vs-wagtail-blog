# vsproject/urls.py
import vsproject.api  # noqa: F401 — registers custom Wagtail v3 API routers before api.urls is accessed

from wagtail.api.v3 import urls as wagtailapi_v3_urls

from blog import views as blog_views
from blog.feeds import BlogRSSFeed
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.decorators.cache import cache_page
from django.views.generic import RedirectView, TemplateView
from vsproject.media_views import serve_public_media
from search import views as search_views
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.contrib.sitemaps.views import sitemap
from wagtail.documents import urls as wagtaildocs_urls
from wagtail_simple_forum.legacy_urls import urlpatterns as legacy_forum_urls

# Slugs may contain Cyrillic characters.
UNICODE_SLUG_REGEX = r"[\wа-яА-Я-]+"

# 404 в оформлении домена (nova/zenway/floppy), см. blog.views.custom_404.
handler404 = "blog.views.custom_404"

urlpatterns = [
    # System and admin routes
    path("robots.txt", TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
    path("sitemap.xml", sitemap),
    path("django-admin/", admin.site.urls),
    path("admin/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("api/v3/", wagtailapi_v3_urls.api.urls),

    # Личные фрагменты (private/no-store): никогда не кэшируются публично
    path("personal/menu/", blog_views.personal_menu, name="personal_menu"),
    path("personal/csrf/", blog_views.personal_csrf, name="personal_csrf"),
    path("personal/admin-bar/<int:page_id>/", blog_views.personal_admin_bar, name="personal_admin_bar"),

    # Search, authentication, feeds, archive
    path("search/", search_views.search, name="search"),
    path("accounts/", include("allauth.urls")),
    path("feed/", cache_page(60 * 15)(BlogRSSFeed()), name="blog_rss_feed"),
    # RSS-фид без слеша: читатели старого сайта (Tiny Tiny RSS и др.)
    # стучатся на /feed — ~7k запросов/день на zenway, все 404. APPEND_SLASH
    # тут не срабатывает (catch-all Wagtail, см. /reader ниже); 301 —
    # агрегаторы запомнят новый адрес (решение владельца, 09.11).
    path("feed", RedirectView.as_view(url="/feed/", permanent=True)),
    path("archive/", blog_views.archive_view, name="archive_view"),

    # Plugin routes
    path("comments/", include("wagtail_mptt_comments.urls")),


    # Tag and category indexes
    re_path(rf"^tag/(?P<slug>{UNICODE_SLUG_REGEX})/?$", blog_views.tag_view, name="tag_view"),
    re_path(rf"^category/(?P<slug>{UNICODE_SLUG_REGEX})/?$", blog_views.category_view, name="category_view"),

    # Permanent redirect for old article URLs (/page/slug/ -> /slug/)
    re_path(rf"^page/(?P<slug>{UNICODE_SLUG_REGEX})/?$", RedirectView.as_view(url="/%(slug)s/", permanent=True)),

    # Forum and legacy forum URLs
    path("forum/", include("wagtail_simple_forum.urls")),
    *legacy_forum_urls,

    # Shared-hosting media route: та же раздача, но с браузерным кэшированием,
    # nosniff и sandbox-политикой для активных типов. См. media_views.py.
    re_path(r"^media/(?P<path>.*)$", serve_public_media),
]

# Optional private LangReader integration; public installs do not need the package.
if getattr(settings, "ENABLE_LANGREADER", False):
    urlpatterns.extend([
        path("reader/", include("wagtail_langreader.urls")),
        path("reader", RedirectView.as_view(url="/reader/", permanent=False)),
    ])

# Development static/media helpers must also precede Wagtail's catch-all.
if settings.DEBUG:
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns
    urlpatterns += staticfiles_urlpatterns()

# Wagtail catch-all MUST remain last.
urlpatterns.append(path("", include(wagtail_urls)))
