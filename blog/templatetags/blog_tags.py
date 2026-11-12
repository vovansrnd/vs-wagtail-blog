# blog/blog_tags.py
from .image_markup import optimize_content_images

from django import template
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
from mptt.utils import get_cached_trees
from taggit.models import Tag
from wagtail.models import Page, Site

from blog.cache_utils import get_blog_cache_epoch
from blog.models import BlogPage, BlogTag, Category

register = template.Library()
register.filter("optimize_content_images", optimize_content_images)


# -----------------------------------------------------------------------------
# 1. АВАТАР ПОЛЬЗОВАТЕЛЯ (Устраняет KeyError: 'user_avatar_url')
# -----------------------------------------------------------------------------
@register.simple_tag
def user_avatar_url(user, size=80):
    """
    Универсальный тег для получения URL аватара пользователя.
    Использование: {% user_avatar_url user 40 as avatar_url %}
    """
    if not user or not getattr(user, "is_authenticated", False):
        return f"{settings.STATIC_URL}img/guest_avatar.png"
    try:
        if hasattr(user, "profile"):
            if hasattr(user.profile, "avatar") and user.profile.avatar:
                return user.profile.avatar.url
            if hasattr(user.profile, "get_avatar_url"):
                return user.profile.get_avatar_url()
    except Exception:
        pass
    return f"{settings.STATIC_URL}img/default_avatar.png"


# -----------------------------------------------------------------------------
# 2. ПОСЛЕДНИЕ ПОСТЫ ПО ТЕГУ (Для mini_posts)
# -----------------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def get_latest_posts_by_tag(context, tag_slug, count=10):
    request = context.get("request")
    site = Site.find_for_request(request) if request else None
    if site is None:
        return []
    cache_key = f"blog:{get_blog_cache_epoch()}:latest-posts-tag:{site.id}:{tag_slug}:{count}"
    cached = cache.get(cache_key)

    if cached is None:
        qs = (
            BlogPage.objects.live()
            .public()
            .filter(tags__slug=tag_slug)
            .order_by("-date_published")
            .defer("body")   # карточки не рендерят body: без него кэш легче в разы
        )
        qs = qs.in_site(site).select_related("main_image")
        cached = list(qs[: int(count)])
        cache.set(cache_key, cached, 60 * 15)

    return cached


# -----------------------------------------------------------------------------
# 3. ПОСЛЕДНИЕ ТЕМЫ ФОРУМА (С поддержкой нового и старого форума)
# -----------------------------------------------------------------------------
@register.simple_tag
def get_latest_forum_topics(count=5):
    cache_key = f"latest_forum_topics_{count}"
    try:
        cached = cache.get(cache_key)
    except Exception:
        # В файловом кэше могут лежать пиклы моделей из другого состояния
        # INSTALLED_APPS (после переезда форума) — считаем это промахом
        cached = None
    if cached is not None:
        return cached

    try:
        from wagtail_simple_forum.models import ForumTopic

        topics = list(
            ForumTopic.objects.filter(is_approved=True)
            .select_related("section", "author")
            .order_by("-last_post_at", "-created_at")[:count]
        )
    except (ImportError, RuntimeError):
        # ImportError — плагин не установлен; RuntimeError — установлен,
        # но процесс стартовал раньше, чем плагин попал в INSTALLED_APPS
        # (старые воркеры Apache до перезагрузки). Сайт не должен падать.
        topics = []

    cache.set(cache_key, topics, 60)  # Кэш на 1 минуту
    return topics


# -----------------------------------------------------------------------------
# 4. ПОСЛЕДНИЕ КОММЕНТАРИИ (Из нового плагина)
# -----------------------------------------------------------------------------
try:
    from wagtail_mptt_comments.templatetags.mptt_comment_tags import get_latest_comments

    register.simple_tag(
        get_latest_comments, takes_context=True, name="get_latest_comments"
    )
except ImportError:
    pass


# -----------------------------------------------------------------------------
# 5. ДЕРЕВО КАТЕГОРИЙ
# -----------------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def get_categories_for_tree(context):
    request = context.get("request")
    site = Site.find_for_request(request) if request else None
    current_slug = (
        context.get("page").slug if getattr(context.get("page"), "slug", None) else ""
    )
    if site is None:
        return {"nodes": [], "current_slug": current_slug}
    site_id = site.id

    cache_key = f"blog:{get_blog_cache_epoch()}:categories-tree:site_{site_id}:all"
    cached_nodes = cache.get(cache_key)

    if cached_nodes is None:
        qs = Category.objects.all().order_by("tree_id", "lft")
        live_pages = BlogPage.objects.live().public().in_site(site)
        qs = qs.annotate(
            num_posts=Count(
                "blogpage", filter=Q(blogpage__in=live_pages), distinct=True
            )
        )

        tree = get_cached_trees(list(qs))

        def prune(nodes):
            visible = []
            for node in nodes:
                pruned_children = prune(getattr(node, "_cached_children", []))
                node.visible_children = pruned_children
                if getattr(node, "num_posts", 0) > 0 or pruned_children:
                    visible.append(node)
            return visible

        cached_nodes = prune([n for n in tree if n.is_root_node()])
        cache.set(cache_key, cached_nodes, 60 * 60)

    return {"nodes": cached_nodes, "current_slug": current_slug}


# -----------------------------------------------------------------------------
# 6. ПОСЛЕДНИЕ ПОСТЫ
# -----------------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def get_latest_posts(context, count=5):
    request = context.get("request")
    site = Site.find_for_request(request) if request else None
    if site is None:
        return []
    site_id = site.id

    cache_key = f"blog:{get_blog_cache_epoch()}:latest-posts:site_{site_id}:{count}"
    cached_posts = cache.get(cache_key)

    if cached_posts is None:
        qs = (
            BlogPage.objects.live()
            .public()
            .select_related("main_image")
            .defer("body")
            .order_by("-date_published")
        )
        qs = qs.in_site(site)
        cached_posts = list(qs[:count])
        cache.set(cache_key, cached_posts, 60 * 15)

    return cached_posts


# -----------------------------------------------------------------------------
# 7. ОБЛАКО ТЕГОВ
# -----------------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def get_tags_for_cloud(context, section="tech"):
    request = context.get("request")
    site = Site.find_for_request(request) if request else None
    if site is None:
        return []
    site_id = site.id

    cache_key = f"blog:{get_blog_cache_epoch()}:tags-cloud:site_{site_id}:{section}"
    cached_tags = cache.get(cache_key)
    if cached_tags is not None:
        return cached_tags

    pages = BlogPage.objects.live().public().in_site(site)

    life_index = Page.objects.in_site(site).filter(slug="life").first()
    if life_index:
        pages = (
            pages.descendant_of(life_index)
            if section == "life"
            else pages.not_descendant_of(life_index)
        )

    tag_counts = list(
        BlogTag.objects.filter(content_object_id__in=pages.values_list("id", flat=True))
        .values("tag_id")
        .annotate(num_times=Count("tag_id"))
        .order_by("-num_times")[:100]
    )

    counts_dict = {item["tag_id"]: item["num_times"] for item in tag_counts}
    tags_list = list(
        Tag.objects.filter(id__in=counts_dict.keys())
        .exclude(slug__isnull=True)
        .exclude(slug="")
        .order_by("name")
    )

    for tag in tags_list:
        tag.num_times = counts_dict.get(tag.id, 0)

    if tags_list:
        min_count, max_count = (
            min(t.num_times for t in tags_list),
            max(t.num_times for t in tags_list),
        )
        for tag in tags_list:
            tag.weight = (
                3
                if max_count == min_count
                else 1
                + round(4 * (tag.num_times - min_count) / (max_count - min_count))
            )

    cache.set(cache_key, tags_list, 60 * 60)
    return tags_list


# -----------------------------------------------------------------------------
# 8. УТИЛИТЫ И ХЕЛПЕРЫ
# -----------------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def get_latest_posts_by_category(context, category_slug, count=10, owner_id=None):
    request = context.get("request")
    site = Site.find_for_request(request) if request else None
    if site is None:
        return []
    cache_key = (
        f"blog:{get_blog_cache_epoch()}:latest-posts-category:"
        f"{site.id}:{category_slug}:{count}:{owner_id}"
    )
    cached_result = cache.get(cache_key)

    if cached_result is None:
        qs = (
            BlogPage.objects.live()
            .public()
            .filter(categories__slug=category_slug)
            .select_related("owner", "main_image")
            .defer("body")
            .order_by("-date_published")
            .in_site(site)
        )
        if owner_id not in (None, "", "None"):
            try:
                qs = qs.filter(owner_id=int(owner_id))
            except (TypeError, ValueError):
                pass
        cached_result = list(qs[: int(count)])
        cache.set(cache_key, cached_result, 60 * 15)

    return cached_result


@register.simple_tag
def smart_page_range(page_obj, on_each_side=2, on_ends=1):
    return [
        "..." if item == page_obj.paginator.ELLIPSIS else item
        for item in page_obj.paginator.get_elided_page_range(
            number=page_obj.number, on_each_side=int(on_each_side), on_ends=int(on_ends)
        )
    ]


@register.filter
def ru_pluralize(value, forms="статья,статьи,статей"):
    one, few, many = forms.split(",")
    n = abs(int(value))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    query = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return f"?{query.urlencode()}" if query else "?"


# ==========================================================
# Фильтр add_attr — добавляет/заменяет атрибут у поля формы
# ==========================================================
@register.filter(name="add_attr")
def add_attr(field, arg):
    """
    Пример использования в шаблоне:
      {{ form.login|add_attr:"placeholder:Логин или email" }}
      {{ form.password|add_attr:"placeholder:Пароль" }}
      {{ form.email|add_attr:"maxlength:50,rows:5" }}
    Формат аргумента: "имя_атрибута:значение[,имя2:знач2,…]"
    """
    # разбираем строку "placeholder:что-то,clasѕ:xxx"
    attrs = {}
    for bit in arg.split(","):
        try:
            name, val = bit.split(":", 1)
        except ValueError:
            continue
        attrs[name.strip()] = val.strip()
    return field.as_widget(attrs=attrs)


# -----------------------------------------------------------------------------
# Проверка настроенного social-провайдера (allauth). provider_login_url на
# несуществующем SocialApp даёт DoesNotExist -> 500 на ВСЕХ страницах темы.
# -----------------------------------------------------------------------------
from allauth.socialaccount.models import SocialApp  # noqa: E402


@register.filter
def social_provider_enabled(provider: str) -> bool:
    """Использование: {% if 'yandex'|social_provider_enabled %} — фильтр,
    потому что simple_tag с аргументом недопустим внутри {% if %}."""
    return SocialApp.objects.filter(provider=provider).exists()
