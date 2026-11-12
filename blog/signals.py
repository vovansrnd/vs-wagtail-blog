# blog/signals.py
from django.core.cache import caches
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from taggit.models import Tag
from wagtail.models import Site
from wagtail.signals import page_published, page_unpublished, page_slug_changed, post_page_move

from .cache_utils import bump_blog_cache_epoch
from .models import BlogPage, BlogTag, Category


def clear_all_caches(sender_name=""):
    # Switch the namespace first: this invalidates every project-owned cache key,
    # including keys whose count/tag/category parameters are not known here.
    bump_blog_cache_epoch()

    # Прикладные ключи с явными именами удаляем адресно; page-кэш и остальные
    # записи default-кэша инвалидируются сменой эпохи (cache_utils → persistent).
    site_ids = list(Site.objects.values_list("id", flat=True))

    keys_to_delete = ["latest_posts_5", "latest_forum_topics_5", "latest_forum_topics_3"]
    for site_id in site_ids:
        keys_to_delete.extend([
            f"categories_tree_site_{site_id}_all",
            f"categories_tree_site_{site_id}_tech",
            f"categories_tree_site_{site_id}_life",
            f"tags_cloud_site_{site_id}_tech",
            f"tags_cloud_site_{site_id}_life",
            f"latest_posts_site_{site_id}_5",
        ])

    caches["default"].delete_many(keys_to_delete)


def _clear_after_commit(sender_name):
    transaction.on_commit(lambda: clear_all_caches(sender_name))


@receiver([post_save, post_delete], sender=Category)
def category_changed(sender, instance, **kwargs):
    _clear_after_commit("Category")


@receiver([post_save, post_delete], sender=Tag)
@receiver([post_save, post_delete], sender=BlogTag)
def tag_changed(sender, instance, **kwargs):
    _clear_after_commit("Tag")


# Инвалидация кеша для публикаций не из админки: API-publisher, отложенная
# публикация, программные изменения Wagtail. Хуки after_publish_page покрывают
# только ручные действия в интерфейсе.

def _specific_page(instance):
    try:
        return instance.specific
    except Exception:
        return instance


def _is_or_contains_blog_page(instance):
    page = _specific_page(instance)
    if isinstance(page, BlogPage):
        return True
    try:
        return BlogPage.objects.descendant_of(instance).exists()
    except Exception:
        return False


@receiver(page_published, dispatch_uid="blog.invalidate_cache.page_published")
def _blog_page_published(sender, instance, **kwargs):
    if _is_or_contains_blog_page(instance):
        transaction.on_commit(lambda: clear_all_caches("page_published"))


@receiver(page_unpublished, dispatch_uid="blog.invalidate_cache.page_unpublished")
def _blog_page_unpublished(sender, instance, **kwargs):
    if _is_or_contains_blog_page(instance):
        transaction.on_commit(lambda: clear_all_caches("page_unpublished"))


@receiver(page_slug_changed, dispatch_uid="blog.invalidate_cache.page_slug_changed")
def _blog_page_slug_changed(sender, instance, **kwargs):
    if _is_or_contains_blog_page(instance):
        transaction.on_commit(lambda: clear_all_caches("page_slug_changed"))


@receiver(post_page_move, dispatch_uid="blog.invalidate_cache.post_page_move")
def _blog_page_moved(sender, instance, url_path_before=None, url_path_after=None, **kwargs):
    if url_path_before != url_path_after and _is_or_contains_blog_page(instance):
        transaction.on_commit(lambda: clear_all_caches("page_moved"))


@receiver(post_delete, sender=BlogPage, dispatch_uid="blog.invalidate_cache.blogpage_deleted")
def _blog_page_deleted(sender, instance, **kwargs):
    transaction.on_commit(lambda: clear_all_caches("blogpage_deleted"))
