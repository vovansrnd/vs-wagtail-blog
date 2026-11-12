import logging
import mimetypes

from django.contrib.syndication.views import Feed
from django.utils.feedgenerator import Rss201rev2Feed
from django.utils.html import strip_tags
from wagtail.models import Site
from .models import BlogPage

logger = logging.getLogger(__name__)


class BlogRSSFeed(Feed):
    feed_type = Rss201rev2Feed

    def get_object(self, request):
        """Определяем Wagtail-сайт по домену запроса"""
        return Site.find_for_request(request)

    def title(self, site):
        if site:
            host = (site.hostname or "").lower()
            if "zenway" in host:
                return "ZenWay.ru — GNU/Linux это интересно"
            if "floppy" in host:
                return "Floppy games — обзоры маленьких игр"
        return "Vs Svet - блог радиолюбителя"

    def description(self, site):
        if site:
            host = (site.hostname or "").lower()
            if "zenway" in host:
                return "Обзоры Linux-программ, дистрибутивов и утилит"
            if "floppy" in host:
                return "Обзоры старых и маленьких игр"
        return "Последние записи блога радиолюбителя"

    def link(self, site):
        if site and site.root_url:
            return site.root_url
        return "/"

    def items(self, site):
        if site is None:
            return BlogPage.objects.none()

        return (
            BlogPage.objects.live()
            .public()
            .in_site(site)
            .select_related("main_image")
            .order_by("-date_published")
            .prefetch_related("categories")[:20]
        )

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        if item.excerpt:
            return strip_tags(str(item.excerpt))
        if item.body:
            return strip_tags(str(item.body))[:500] + "..."
        return ""

    def item_link(self, item):
        return item.full_url or item.url

    def item_pubdate(self, item):
        return item.date_published

    def item_author_name(self, item):
        return "Автор блога"

    def item_categories(self, item):
        return [category.name for category in item.categories.all()]

    def item_enclosure_url(self, item):
        if item.main_image and hasattr(item.main_image, 'file'):
            return item.main_image.file.url
        return None

    def item_enclosure_length(self, item):
        image = getattr(item, "main_image", None)
        if not image:
            return 0
        # Wagtail stores file_size on the image row. This avoids storage I/O on
        # the common path; only old/imported rows without a size need a fallback.
        size = getattr(image, "file_size", None)
        if size:
            return size
        try:
            return image.file.size if image.file else 0
        except Exception:
            # Remote storage backends can raise provider-specific exceptions.
            # A broken/missing enclosure must not turn the entire RSS feed into 500.
            logger.debug("Could not obtain RSS enclosure size for image %s", image.pk, exc_info=True)
            return 0

    def item_enclosure_mime_type(self, item):
        if not item.main_image or not item.main_image.file:
            return None
        # Main images are not guaranteed to be JPEG; report the storage filename's
        # media type rather than emitting a misleading enclosure type.
        content_type, _encoding = mimetypes.guess_type(item.main_image.file.name)
        return content_type or "application/octet-stream"
