# blog/views.py
import logging
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count, OuterRef, Subquery, IntegerField
from django.db.models.functions import Coalesce
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template import loader
from django.views.decorators.csrf import ensure_csrf_cookie
from django.template.response import TemplateResponse
from taggit.models import Tag
from wagtail.models import Site
from .models import BlogPage, Category
from wagtail_mptt_comments.models import Comment
from .utils import get_domain_theme

logger = logging.getLogger(__name__)


def personal_menu(request):
    """Авторизованное меню шапки — приватный фрагмент для JS-подмены.

    Публичные страницы кэшируются с анонимным меню по умолчанию; браузер
    пользователя с сессией подменяет блок через этот эндпоинт.
    Ответ private/no-store и никогда не попадает в общий кэш.
    """
    if not request.user.is_authenticated:
        resp = JsonResponse({"authenticated": False})
        resp["Cache-Control"] = "private, no-store"
        return resp
    resp = render(request, ["blog/personal_menu.html"], {})
    resp["Cache-Control"] = "private, no-store"
    return resp


def personal_admin_bar(request, page_id):
    """Админ-бар статьи (кнопка «Редактировать») для персонала.

    На кэшируемых страницах бара нет; для сотрудника подставляется
    приватным фрагментом по page_id. Остальные получают 403.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        resp = JsonResponse({"authenticated": False})
        resp["Cache-Control"] = "private, no-store"
        return resp
    edit_url = f"/admin/pages/{int(page_id)}/edit/"
    resp = render(request, ["blog/personal_admin_bar.html"], {"edit_url": edit_url})
    resp["Cache-Control"] = "private, no-store"
    return resp


@ensure_csrf_cookie
def personal_csrf(request):
    """Гарантирует наличие CSRF-cookie для JS-форм (подписка и т.п.).

    Страницы больше не отдают Set-Cookie: csrftoken (это мешало общему
    кэшу), cookie выдаётся лениво этим приватным эндпоинтом.
    """
    resp = JsonResponse({"ok": True})
    resp["Cache-Control"] = "private, no-store"
    return resp


def custom_404(request, exception=None):
    """404 в оформлении домена: nova — корневой 404.html, zenway/floppy — свои.

    Django по умолчанию берёт единственный 404.html из DIRS, поэтому на
    zenway.ru страница ошибки выглядела как vs-svet.
    """
    theme = get_domain_theme(request)
    template = loader.select_template([f"blog/{theme}/404.html", "404.html"])
    return TemplateResponse(request, template, {}, status=404)


def _with_approved_comment_counts(queryset):
    """Annotate preview rows without per-post count queries."""
    counts = (
        Comment.objects.filter(page=OuterRef("pk"), is_approved=True)
        .order_by()
        .values("page")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )
    return queryset.annotate(
        approved_comments_count=Coalesce(
            Subquery(counts, output_field=IntegerField()), 0
        )
    )

def tag_view(request, slug):
    tag = get_object_or_404(Tag, slug__iexact=slug)
    site = Site.find_for_request(request)
    if site is None:
        raise Http404("Для этого домена не настроен Wagtail Site.")

    blogpages_qs = _with_approved_comment_counts(
        BlogPage.objects.live().public()
        .in_site(site)
        .filter(tags=tag)
        .order_by("-first_published_at")
        .select_related("main_image")
        .prefetch_related("tags", "categories")
    )

    paginator = Paginator(blogpages_qs, 10)
    try:
        posts = paginator.page(request.GET.get("page", 1))
    except (PageNotAnInteger, EmptyPage):
        raise Http404("Страница не существует.")

    theme = get_domain_theme(request)
    return render(request, f"blog/{theme}/tag_index.html", {"tag": tag, "posts": posts})


def category_view(request, slug):
    category = get_object_or_404(Category, slug__iexact=slug)
    site = Site.find_for_request(request)
    if site is None:
        raise Http404("Для этого домена не настроен Wagtail Site.")

    blogpages_qs = _with_approved_comment_counts(
        BlogPage.objects.live().public()
        .in_site(site)
        .filter(categories=category)
        .order_by("-first_published_at")
        .select_related("main_image")
        .prefetch_related("tags", "categories")
    )

    paginator = Paginator(blogpages_qs, 10)
    try:
        posts = paginator.page(request.GET.get("page", 1))
    except (PageNotAnInteger, EmptyPage):
        raise Http404("Страница не существует.")

    theme = get_domain_theme(request)
    return render(request, f"blog/{theme}/category_index.html", {"category": category, "posts": posts})


def archive_view(request):
    site = Site.find_for_request(request)
    posts = _with_approved_comment_counts(
        BlogPage.objects.live().public().order_by("-date_published").select_related("main_image")
    )
    posts = posts.in_site(site) if site else BlogPage.objects.none()

    paginator = Paginator(posts, 50)
    try:
        page_obj = paginator.page(request.GET.get("page", 1))
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(paginator.num_pages)

    theme = get_domain_theme(request)
    return render(request, f"blog/{theme}/archive.html", {"posts": page_obj})
