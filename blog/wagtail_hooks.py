# blog/wagtail_hooks.py
from django.urls import reverse
from django.utils.html import format_html
from taggit.models import Tag
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet
from django.templatetags.static import static

from .models import Category

@register_snippet
class CategoryAdmin(SnippetViewSet):
    model = Category
    menu_label = "Рубрики"
    icon = "folder-open-1"
    list_display = ("name", "slug", "parent")
    list_filter = ("parent",)
    search_fields = ("name", "slug")

@register_snippet
class TagAdmin(SnippetViewSet):
    model = Tag
    menu_label = "Теги"
    icon = "tag"
    list_display = ("name", "slug")
    search_fields = ("name", "slug")

@hooks.register("register_admin_menu_item")
def register_articles_menu_item():
    # ФИКС: Используем системный эксплорер Wagtail, а не жесткий ID 4!
    return MenuItem("Дерево страниц", reverse("wagtailadmin_explore_root"), icon_name="doc-full-inverse", order=15)

@hooks.register("insert_global_admin_js")
def global_admin_js():
    return format_html(
        '<script src="{}" defer></script>\n'
        '<script src="{}" defer></script>\n'
        '<script src="{}" defer></script>',
        static("js/prose_editor_codeblock.js"),
        static("js/prose_editor_media.js"),
        static("js/admin_prose_editor_sticky.js"),
    )

@hooks.register("insert_global_admin_css")
def global_admin_css():
    return format_html('<link rel="stylesheet" href="{}">', static("css/admin_prose_editor.css"))
