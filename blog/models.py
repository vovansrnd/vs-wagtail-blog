# blog/models.py
import logging
import secrets
import time
from datetime import datetime
from uuid import uuid4

from django import forms
from django.conf import settings
from django.core.cache import cache
from django.core import signing
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import Http404
from django.db import models
from django.db.models import Count, Max, Prefetch, Q, OuterRef, Subquery, IntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_prose_editor.fields import ProseEditorField
from django_prose_editor.widgets import AdminProseEditorWidget
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.fields import ParentalKey, ParentalManyToManyField
from mptt.models import MPTTModel, TreeForeignKey
from taggit.models import TaggedItemBase
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, FieldRowPanel, InlinePanel, MultiFieldPanel
from wagtail.api import APIField
from wagtail.contrib.forms.forms import FormBuilder
from wagtail.contrib.forms.models import AbstractEmailForm, AbstractFormField
from wagtail.contrib.settings.models import BaseSiteSetting
from wagtail.contrib.settings.registry import register_setting
from wagtail.fields import RichTextField, StreamField
from wagtail.images.models import AbstractRendition, Image
from wagtail.images import get_image_model_string
from wagtail.models import Page
from wagtail.search import index

# Импорты из плагинов
from wagtail_image_directories.models import AbstractDirectoryImage
from wagtail_mptt_comments.models import Comment

from .cache_utils import get_blog_cache_epoch
from .utils import get_domain_theme

logger = logging.getLogger(__name__)

class UsedFormToken(models.Model):
    """Одноразовые токены контактной формы — DB-backed replay protection.

    Хранится хеш nonce, не сам токен. Уникальность гарантирует, что повторный
    submit с тем же nonce будет отклонён на уровне БД даже после вытеснения
    security-кэша. Просроченные записи удаляются командой cleanup_expired_tokens.
    """

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Использованный токен формы"

    def __str__(self):
        return self.token_hash[:16]


class InvalidationEpoch(models.Model):
    """Единственная строка (pk=1) с текущей эпохой инвалидации блога.

    Не кэш: не вытесняется, переживает restart и cache.clear().
    Чтение — один SELECT по pk. Запись — атомарный UPDATE (get_or_create + update).
    """

    epoch = models.CharField(max_length=64, default="initial")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Эпоха инвалидации"
        constraints = [
            # Singleton: запрещает вторую строку (pk=2 и т.д.), PK запрещает
            # только дубликат pk=1. Проверяется на уровне БД.
            models.CheckConstraint(
                condition=models.Q(id=1),
                name="invalidation_epoch_singleton",
            ),
        ]

    def __str__(self):
        return f"epoch={self.epoch[:12]}"

    # Однократный лог на процесс, чтобы не затопить security.log при
    # продолжительном отсутствия строки (восстановление — контролируемая
    # процедура/миграция, а не скрытый fallback в каждом запросе).
    _bypass_logged = False

    @classmethod
    def _safe_bypass_epoch(cls, level: str, message: str) -> str:
        """Уникальное значение = гарантированный bypass page cache.

        Никогда не совпадает с закэшированным namespace: каждый запрос
        получает MISS, пока строка не восстановлена миграцией 0039.
        Возврат "initial" при отсутствии строки запрещён: он позволил бы
        отдать устаревший namespace.
        """
        if not cls._bypass_logged:
            getattr(logger, level)(message)
            cls._bypass_logged = True
        return f"bypass-{uuid4().hex}"

    @classmethod
    def get_epoch(cls) -> str:
        try:
            return cls.objects.only("epoch").get(pk=1).epoch
        except cls.DoesNotExist:
            return cls._safe_bypass_epoch(
                "error",
                "InvalidationEpoch pk=1 отсутствует: page cache безопасно "
                "обойдён (bypass). Восстановите строку миграцией 0039.",
            )
        except Exception:
            logger.exception(
                "InvalidationEpoch недоступна (сбой БД): page cache безопасно обойдён"
            )
            return f"bypass-{uuid4().hex}"

    @classmethod
    def bump(cls) -> str:
        new = uuid4().hex
        updated = cls.objects.filter(pk=1).update(epoch=new)
        if not updated:
            from django.db import IntegrityError, transaction
            try:
                # Внутренний atomic() = savepoint: IntegrityError от гонки
                # не оставляет внешнюю транзакцию в broken state (тот же
                # паттерн, что и у UsedFormToken в SpamProtectedForm).
                with transaction.atomic():
                    cls.objects.create(pk=1, epoch=new)
            except IntegrityError:
                # Проиграли гонку другому bump(): перезаписываем его значение.
                cls.objects.filter(pk=1).update(epoch=new)
        return new


COMMENTS_EXTENSIONS = {
    "Bold": True, "Italic": True, "Blockquote": True, "BulletList": True,
    "ListItem": True, "OrderedList": True, "Link": {"enableTarget": True},
    "Code": True, "CodeBlock": True,
}

ADMIN_EXTENSIONS_WITH_WAGTAIL_IMAGES = COMMENTS_EXTENSIONS | {
    # "Clear" убран: санитайзер django-prose-editor 0.27.x не знает такого
    # расширения (кнопка не создаёт HTML), check падает KeyError при sanitize=True
    "Heading": {"levels": [2, 3]}, "Image": True, "History": True,
    "HTML": True, "Strike": True, "Underline": True, "HardBreak": True,
    "Typographic": True, "Subscript": True, "Superscript": True, "HorizontalRule": True,
}

# ВАЖНО: Функция нужна для старых миграций Django! Не удалять!
def get_current_year():
    return datetime.now().year

# ==========================================================
# 1. МОДЕЛИ ИЗОБРАЖЕНИЙ
# ==========================================================
class CustomImage(AbstractDirectoryImage):
    """
    Кастомная модель картинок: наследует логику папок (year/slug)
    из плагина wagtail-image-directories и блокирует лишние кропы!
    """
    rendition_model = "blog.CustomRendition"

    def get_rendition(self, filter):
        # Ограниченная политика рендишенов для экономии диска. Спецификации в
        # allowlist возвращают исходный файл, а не реальные пиксели crop/resize;
        # не добавляйте сюда публичные размеры без оценки трафика и результата.
        admin_specs = [
            "max-165x165",
            "width-800",
            "fill-600x338",
            "original",
            "max-150x150",
            # Deliberate legacy pass-through allowlist: these specs return the
            # original file rather than a true rendition to limit disk use. Do not
            # add public-delivery specs casually; first check templates and measure
            # the storage cost. Open Graph (fill-1200x630) and article display
            # (max-800x600) are deliberately excluded so they create real renditions.
            "width-800-c100",
            "fill-90x90",
        ]

        if isinstance(filter, str):
            filter_spec = filter
        elif hasattr(filter, "spec"):
            filter_spec = filter.spec
        else:
            return super().get_rendition(filter)

        if filter_spec in admin_specs:
            try:
                RenditionModel = self.get_rendition_model()
                dummy = RenditionModel(
                    image=self,
                    filter_spec=filter_spec,
                    width=self.width,
                    height=self.height,
                    focal_point_key="",
                )
                dummy.file = self.file
                return dummy
            except Exception as e:
                logger.error(f"Error creating dummy rendition: {e}")
                return super().get_rendition(filter)

        return super().get_rendition(filter)

    class Meta(AbstractDirectoryImage.Meta):
        verbose_name = "Изображение"
        verbose_name_plural = "Изображения"

class CustomRendition(AbstractRendition):
    image = models.ForeignKey(get_image_model_string(), on_delete=models.CASCADE, related_name="renditions")
    class Meta:
        unique_together = (("image", "filter_spec", "focal_point_key"),)

# ==========================================================
# 2. ТЕГИ И КАТЕГОРИИ
# ==========================================================
class BlogTag(TaggedItemBase):
    content_object = ParentalKey("BlogPage", related_name="tagged_items", on_delete=models.CASCADE)

class Category(MPTTModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    parent = TreeForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    panels = [FieldPanel("name"), FieldPanel("slug"), FieldPanel("parent")]
    class MPTTMeta:
        order_insertion_by = ["name"]
    def __str__(self):
        return self.name

# ==========================================================
# 3. СТРАНИЦА БЛОГА
# ==========================================================
class BlogPage(Page):
    def get_template(self, request, *args, **kwargs):
        theme = get_domain_theme(request)
        return f"blog/{theme}/blog_page.html"

    body = ProseEditorField(extensions=ADMIN_EXTENSIONS_WITH_WAGTAIL_IMAGES, blank=True, null=True, sanitize=True)
    excerpt = ProseEditorField(extensions=COMMENTS_EXTENSIONS, blank=True, sanitize=True, verbose_name="Краткое описание")
    date_published = models.DateTimeField(default=timezone.now, verbose_name="Дата публикации", db_index=True)

    categories = ParentalManyToManyField("blog.Category", blank=True, verbose_name="Рубрики")
    tags = ClusterTaggableManager(through="blog.BlogTag", blank=True, verbose_name="Теги")
    main_image = models.ForeignKey("blog.CustomImage", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    seo_description = models.CharField(max_length=160, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)

    # Поля статьи в Wagtail v3 API: для записи нужны APIField(writable=True)
    # (строки в api_fields дают только чтение). body/excerpt — ProseEditorField,
    # пишутся как HTML-строка (markdown→HTML конвертирует publisher).
    api_fields = [
        APIField("body", writable=True),
        APIField("excerpt", writable=True),
        APIField("main_image", writable=True),
        APIField("date_published", writable=True),
        APIField("categories", writable=True),
        APIField("tags", writable=True),
        APIField("seo_description", writable=True),
        APIField("seo_keywords", writable=True),
    ]

    # Keep Page's title SearchField (partial_match=True, boost=2) instead
    # of declaring a second SearchField for the same field. Wagtail indexes
    # should inherit the built-in title definition only once.
    search_fields = Page.search_fields + [
        index.SearchField("body"), index.FilterField("date_published"),
        index.RelatedFields("categories", [index.SearchField("name")]),
    ]

    content_panels = [
        FieldPanel("title", classname="full title"), FieldPanel("slug"), FieldPanel("main_image"),
        FieldPanel("date_published"), FieldPanel("categories", widget=forms.CheckboxSelectMultiple), FieldPanel("tags"),
        FieldPanel("excerpt", widget=AdminProseEditorWidget(preset="configurable", config={"extensions": COMMENTS_EXTENSIONS})),
        FieldPanel("body", widget=AdminProseEditorWidget(preset="configurable", config={"extensions": ADMIN_EXTENSIONS_WITH_WAGTAIL_IMAGES})),
    ]
    promote_panels = [FieldPanel("seo_title"), FieldPanel("show_in_menus"), FieldPanel("seo_description"), FieldPanel("seo_keywords")]

    def save(self, *args, **kwargs):
        # Sanitize-гейт для всех путей записи, минующих формы (API, импорт):
        # форма админки чистит поле своим sanitize=True, здесь — nh3 с тем же
        # контрактом. Двойная очистка безопасна: nh3 идемпотентен.
        from .html_sanitizer import sanitize_html
        if self.body:
            self.body = sanitize_html(str(self.body))
        if self.excerpt:
            self.excerpt = sanitize_html(str(self.excerpt))
        super().save(*args, **kwargs)

    @property
    def meta_description(self) -> str:
        """Приоритет: seo_description → search_description → excerpt (без тегов).

        Редакторское поле seo_description не входит в Wagtail search_description,
        поэтому шаблоны, выводящие search_description, оставались пустыми.
        """
        import re as _re

        def _clean(text):
            return _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", "", str(text or ""))).strip()

        for source in (self.seo_description, self.search_description, self.excerpt):
            text = _clean(source)
            if text:
                return text[:160]
        return ""

    def get_prev_post(self):
        """Предыдущая статья; id служит стабильным tie-break при одинаковой дате."""
        site = self.get_site()
        qs = BlogPage.objects.live().public().filter(
            Q(date_published__lt=self.date_published)
            | Q(date_published=self.date_published, pk__lt=self.pk)
        )
        if site is not None:
            qs = qs.descendant_of(site.root_page)
        return qs.order_by("-date_published", "-pk").only(
            "id", "title", "slug", "date_published"
        ).first()

    def get_next_post(self):
        """Следующая статья; id служит стабильным tie-break при одинаковой дате."""
        site = self.get_site()
        qs = BlogPage.objects.live().public().filter(
            Q(date_published__gt=self.date_published)
            | Q(date_published=self.date_published, pk__gt=self.pk)
        )
        if site is not None:
            qs = qs.descendant_of(site.root_page)
        return qs.order_by("date_published", "pk").only(
            "id", "title", "slug", "date_published"
        ).first()

    def get_category_breadcrumbs(self):
        primary_category = self.categories.order_by("-level", "name").first()
        return primary_category.get_ancestors(include_self=True) if primary_category else []

    def get_similar_posts(self):
        site = self.get_site()
        if site is None:
            return []
        site_id = site.id
        cache_key = f"blog:{get_blog_cache_epoch()}:similar-posts:site_{site_id}:page_{self.id}"
        similar_ids = cache.get(cache_key)

        if similar_ids is None:
            base_qs = BlogPage.objects.live().public().in_site(site).exclude(id=self.id)
            tag_ids = list(self.tags.values_list("id", flat=True)[:5])
            similar_list = list(base_qs.filter(tags__in=tag_ids).distinct()[:6]) if tag_ids else []

            if len(similar_list) < 3:
                category = self.categories.first()
                if category:
                    existing_ids = [p.id for p in similar_list] + [self.id]
                    similar_list.extend(list(base_qs.filter(categories=category).exclude(id__in=existing_ids)[:6 - len(similar_list)]))

            similar_ids = [p.id for p in similar_list]
            cache.set(cache_key, similar_ids, 60 * 60 * 6) # Кэш только ID

        if similar_ids:
            posts = (
                BlogPage.objects.live().public().in_site(site)
                .filter(id__in=similar_ids)
                .select_related("main_image")
            )
            posts_dict = {p.id: p for p in posts}
            return [posts_dict[pid] for pid in similar_ids if pid in posts_dict]
        return []

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["breadcrumbs"] = self.get_category_breadcrumbs()
        context["similar_posts"] = self.get_similar_posts()

        # Public comment templates render local profile avatars and role badges.
        # Load the one-to-one profile and user groups with the comment rows to
        # avoid one or two extra queries per displayed comment.
        approved_comments = (
            Comment.objects.filter(page=self, is_approved=True)
            .select_related("user", "user__profile")
            .prefetch_related("user__groups")
        )
        comment_stats = approved_comments.aggregate(
            total=Count("pk"), max_level=Max("level")
        )
        context["approved_comments_count"] = comment_stats["total"] or 0

        # Nova and ZenWay recursively render `comment.children.all`. Prefetch
        # each tree level only as deep as the actual data requires (MAX_DEPTH is
        # bounded in the Comment model), avoiding one query per rendered node.
        max_level = comment_stats["max_level"] or 0
        child_queryset = (
            Comment.objects.filter(is_approved=True)
            .select_related("user", "user__profile")
            .prefetch_related("user__groups")
            .order_by("tree_id", "lft")
        )
        child_prefetches = []
        child_path = "children"
        for _ in range(min(max_level, Comment.MAX_DEPTH - 1)):
            child_prefetches.append(
                Prefetch(child_path, queryset=child_queryset)
            )
            child_path += "__children"

        context["comment_roots"] = (
            approved_comments.filter(parent=None)
            .prefetch_related(*child_prefetches)
            .order_by("tree_id", "lft")
        )

        # MPTT's recursetree tag needs the complete node set so it can discover
        # descendants. The Floppy theme previously received roots only, so replies
        # were omitted from the rendered tree even though the root comments existed.
        if get_domain_theme(request) == "floppy":
            context["comment_tree"] = approved_comments.order_by(
                "tree_id", "lft"
            )

        # Форма комментария грузится лениво приватным фрагментом
        # (Cache-Control: private, no-store), чтобы статья оставалась
        # кэшируемой: форма обращается к сессии и содержит CSRF/токены.
        # На страницу отдаём только её статические ассеты.
        from wagtail_mptt_comments.forms import PublicCommentForm
        media_form = PublicCommentForm(page=self, request=None)
        context["comment_form_media"] = media_form.media
        import re as _re
        rendered = str(media_form.media)
        m = _re.search(r'src="([^"]*django_prose_editor/configurable[^"]*)"', rendered)
        context["comment_editor_module"] = m.group(1) if m else ""
        return context

# ==========================================================
# 4. ИНДЕКСНАЯ СТРАНИЦА БЛОГА
# ==========================================================
class BlogIndexPage(Page):
    def get_template(self, request, *args, **kwargs):
        theme = get_domain_theme(request)
        return f"blog/{theme}/blog_index_page.html"

    search_page = models.ForeignKey(
        "blog.SearchPage", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Страница поиска"
    )
    content_panels = Page.content_panels + [FieldPanel("search_page")]

    def get_context(self, request):
        context = super().get_context(request)
        site = self.get_site()
        blogpages_qs = BlogPage.objects.live().public().descendant_of(self)
        if site:
            blogpages_qs = blogpages_qs.in_site(site)
        else:
            blogpages_qs = BlogPage.objects.none()

        if site and self.slug != "life":
            life_page = Page.objects.in_site(site).filter(slug="life").first()
            if life_page:
                blogpages_qs = blogpages_qs.not_descendant_of(life_page)

        # Очищаем order_by() и берем [:1], чтобы Postgres гарантированно не выдавал CardinalityViolation
        comments_count_sq = (
            Comment.objects.filter(page=OuterRef('pk'), is_approved=True)
            .order_by()
            .values('page')
            .annotate(count=Count('id'))
            .values('count')[:1]
        )
        blogpages = blogpages_qs.order_by("-first_published_at").select_related("main_image").prefetch_related("tags", "categories").annotate(
            approved_comments_count=Coalesce(Subquery(comments_count_sq, output_field=IntegerField()), 0)
        )

        paginator = Paginator(blogpages, 6)
        try:
            blogs = paginator.page(request.GET.get("page", 1))
        except (PageNotAnInteger, EmptyPage):
            # Invalid page numbers must not silently return page 1. Besides
            # being surprising, that creates duplicate canonical content for
            # crawlers and hides broken pagination links.
            raise Http404("Страница не существует.")

        context["blogs"] = blogs
        latest_posts = BlogPage.objects.live().public()
        latest_posts = latest_posts.in_site(site) if site else BlogPage.objects.none()
        context["latest_posts"] = (
            latest_posts.select_related("main_image").order_by("-date_published")[:5]
        )

        search_pages = SearchPage.objects.live().public()
        search_pages = search_pages.in_site(site) if site else SearchPage.objects.none()
        search_page = search_pages.first()
        context["search_page_url"] = search_page.url if search_page else "/search/"
        return context

# ==========================================================
# 5. СТРАНИЦА ПОИСКА
# ==========================================================
class SearchPage(Page):
    template = "search/search.html"
    max_count_per_parent = 1

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        search_query = request.GET.get("query", None)
        search_results = None

        current_site = self.get_site()
        if search_query and current_site:
            # Wagtail's .search() returns SearchResults, not a normal QuerySet.
            # Paginate the ranked search results first, then hydrate only the
            # visible page through a normal QuerySet for tags, image and counts.
            ranked_results = BlogPage.objects.live().public().in_site(current_site).search(search_query)
            paginator = Paginator(ranked_results, 10)
            search_results = paginator.get_page(request.GET.get("page"))
            ranked_page = list(search_results.object_list)
            page_ids = [result.pk for result in ranked_page]

            if page_ids:
                comments_count_sq = (
                    Comment.objects.filter(page=OuterRef("pk"), is_approved=True)
                    .order_by()
                    .values("page")
                    .annotate(count=Count("id"))
                    .values("count")[:1]
                )
                hydrated = (
                    BlogPage.objects.live().public().in_site(current_site)
                    .filter(pk__in=page_ids)
                    .select_related("main_image")
                    .prefetch_related("tags", "categories")
                    .annotate(
                        approved_comments_count=Coalesce(
                            Subquery(comments_count_sq, output_field=IntegerField()), 0
                        )
                    )
                )
                by_id = {page.pk: page for page in hydrated}
                # QuerySet ordering is not the search ranking; preserve the
                # original order returned by the configured search backend.
                search_results.object_list = [by_id[pk] for pk in page_ids if pk in by_id]
            else:
                search_results.object_list = []

        context["search_query"] = search_query
        context["search_results"] = search_results
        return context

    class Meta:
        verbose_name = "Страница поиска"

# ==========================================================
# 6. КОНТАКТЫ И ФОРМЫ
# ==========================================================
class FormField(AbstractFormField):
    page = ParentalKey("ContactPage", on_delete=models.CASCADE, related_name="form_fields")

class SpamProtectedFormBuilder(FormBuilder):
    """Build contact forms with a signed, short-lived anti-bot token.

    The token is generated server-side; users cannot simply post an old float
    timestamp to bypass the minimum completion time. ContactPage responses are
    marked no-store below so a shared cache does not hand the same one-use token
    to multiple visitors.
    """
    def get_form_class(self):
        form_class = super().get_form_class()

        class SpamProtectedForm(form_class):
            company_website = forms.CharField(
                required=False,
                label="Сайт",
                widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
            )
            form_token = forms.CharField(required=False, widget=forms.HiddenInput())

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                if not self.is_bound:
                    payload = {
                        "issued_at": time.time(),
                        "nonce": secrets.token_urlsafe(18),
                    }
                    self.fields["form_token"].initial = signing.dumps(
                        payload, salt="blog.contact_form", compress=True
                    )

            def clean(self):
                cleaned_data = super().clean()
                if cleaned_data.get("company_website"):
                    raise forms.ValidationError("Спам-активность.")

                token = cleaned_data.get("form_token")
                if not token:
                    raise forms.ValidationError("Не удалось проверить форму. Обновите страницу.")
                max_age = int(getattr(settings, "CONTACT_FORM_TOKEN_MAX_AGE", 24 * 60 * 60))
                try:
                    payload = signing.loads(
                        token, salt="blog.contact_form", max_age=max_age
                    )
                    issued_at = float(payload["issued_at"])
                    nonce = str(payload["nonce"])
                except (signing.BadSignature, KeyError, TypeError, ValueError, AttributeError):
                    raise forms.ValidationError("Форма устарела или имеет неверную подпись. Обновите страницу.")

                age = time.time() - issued_at
                if age < 4 or age > max_age:
                    raise forms.ValidationError("Форма отправлена слишком быстро или устарела.")
                # Keep a valid token reusable while another field has validation
                # errors; consume it only on a fully valid submission.
                if self.errors:
                    return cleaned_data
                # DB-backed replay protection (Этап 2 аудита, переработка):
                # хеш nonce в таблице с unique constraint. Вытеснение любого
                # кэша не разрешает повторный submit. Проверка и регистрация
                # использования атомарны (IntegrityError от unique constraint).
                import hashlib as _hl
                from django.db import IntegrityError, transaction

                def _is_unique_violation(exc):
                    # Отличать нарушение уникальности token_hash от иных
                    # ошибок целостности. ПОЛИТИКА (координатор, Этап А):
                    # replay = ТОЛЬКО подтверждённый 23505/sqlstate
                    # unique-violation фактического драйвера (psycopg2
                    # передаёт sqlstate в __cause__.pgcode — проверено
                    # тестом на реальном нарушении). Отсутствующий или
                    # неизвестный код = проблема хранения -> fail-closed.
                    cause = getattr(exc, "__cause__", None)
                    pgcode = getattr(cause, "pgcode", None)
                    sqlstate = getattr(cause, "sqlstate", None)
                    return "23505" in (pgcode, sqlstate)

                token_hash = _hl.sha256(nonce.encode()).hexdigest()
                try:
                    from .models import UsedFormToken
                    # Внутренний atomic() = savepoint: IntegrityError
                    # не оставляет внешнюю транзакцию в broken state.
                    with transaction.atomic():
                        UsedFormToken.objects.create(token_hash=token_hash)
                except IntegrityError as e:
                    if not _is_unique_violation(e):
                        logger.exception("Replay protection: non-unique IntegrityError")
                        raise forms.ValidationError("Проверка формы временно недоступна. Повторите попытку позже.")
                    raise forms.ValidationError("Эта форма уже была отправлена. Обновите страницу.")
                except Exception:
                    logger.exception("Replay protection DB unavailable")
                    raise forms.ValidationError("Проверка формы временно недоступна. Повторите попытку позже.")
                return cleaned_data

        return SpamProtectedForm

class ContactPage(AbstractEmailForm):
    intro = RichTextField(blank=True)
    thank_you_text = RichTextField(blank=True)
    form_builder = SpamProtectedFormBuilder

    def serve(self, request, *args, **kwargs):
        response = super().serve(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        return response

    def get_template(self, request, *args, **kwargs):
        theme = get_domain_theme(request)
        return f"blog/{theme}/contact_page.html"

    def get_landing_page_template(self, request, *args, **kwargs):
        theme = get_domain_theme(request)
        return f"blog/{theme}/contact_page_landing.html"

    def process_form_submission(self, form):
        self.from_address = settings.DEFAULT_FROM_EMAIL
        if not self.to_address: self.to_address = settings.DEFAULT_FROM_EMAIL
        if not self.subject: self.subject = f"[{getattr(settings, 'SITE_NAME', 'Site')}] Сообщение"
        # Служебные поля не нужны ни в письме уведомления, ни в записи заявки:
        # подписанный form_token — это полэкрана base64 в письме, honeypot
        # company_website всегда пуст. Валидация уже прошла (replay-защита
        # регистрирует nonce в clean(), а не из cleaned_data).
        form.cleaned_data = {
            k: v for k, v in form.cleaned_data.items()
            if k not in ("form_token", "company_website")
        }
        return super().process_form_submission(form)

    content_panels = AbstractEmailForm.content_panels + [
        FieldPanel("intro"), InlinePanel("form_fields", label="Поля"), FieldPanel("thank_you_text"),
        MultiFieldPanel([FieldRowPanel([FieldPanel("from_address", classname="col6"), FieldPanel("to_address", classname="col6")]), FieldPanel("subject")], "Email"),
    ]
    class Meta:
        verbose_name = "Страница: Обратная связь"

# ==========================================================
# 7. ПРОДУКТЫ
# ==========================================================
class FeatureBlock(blocks.StructBlock): title = blocks.CharBlock(); text = blocks.TextBlock(required=False); icon = blocks.CharBlock(required=False)
class SpecItemBlock(blocks.StructBlock): name = blocks.CharBlock(); value = blocks.CharBlock()
class TestimonialBlock(blocks.StructBlock): author = blocks.CharBlock(); quote = blocks.TextBlock(); source = blocks.CharBlock(required=False); url = blocks.URLBlock(required=False); rating = blocks.IntegerBlock(required=False, min_value=1, max_value=5)
class BuyLinkBlock(blocks.StructBlock): label = blocks.CharBlock(default="Авито"); url = blocks.URLBlock(); icon = blocks.CharBlock(required=False)

class ProductPage(Page):
    subtitle = models.CharField(max_length=255, blank=True)
    hero_image = models.ForeignKey(get_image_model_string(), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    intro = RichTextField(blank=True)
    price = models.CharField(max_length=50, blank=True)
    features = StreamField([("feature", FeatureBlock())], blank=True, use_json_field=True)
    specs = StreamField([("item", SpecItemBlock())], blank=True, use_json_field=True)
    testimonials = StreamField([("item", TestimonialBlock())], blank=True, use_json_field=True)
    buy_links = StreamField([("link", BuyLinkBlock())], blank=True, use_json_field=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)

    content_panels = Page.content_panels + [
        FieldPanel("subtitle"), FieldPanel("hero_image"), FieldPanel("price"), FieldPanel("intro"),
        FieldPanel("features"), FieldPanel("specs"), FieldPanel("testimonials"), FieldPanel("buy_links"),
        MultiFieldPanel([FieldPanel("contact_email"), FieldPanel("contact_phone")], heading="Контакты"),
    ]
    template = "blocks/product_page.html"

@register_setting
class BlogSettings(BaseSiteSetting):
    featured_product = models.ForeignKey("wagtailcore.Page", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", limit_choices_to={"content_type__model": "productpage"})
    show_announcement = models.BooleanField(default=False)
    announcement_text = models.CharField(max_length=255, blank=True, default="Анонс")
    announcement_cta = models.CharField(max_length=64, blank=True, default="Подробнее")
