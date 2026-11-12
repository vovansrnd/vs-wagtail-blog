from django.core.paginator import Paginator
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.http import HttpResponseBadRequest
from django.template.response import TemplateResponse
from wagtail.models import Site

from blog.models import BlogPage, Comment

MAX_SEARCH_QUERY_LENGTH = 200
RESULTS_PER_PAGE = 10


def _hydrate_ranked_page(ranked_page, site):
    """Load display relations for only the current ranked result page.

    Wagtail SearchResults is not a normal QuerySet on the PostgreSQL/database
    search backends, so calling select_related/prefetch_related on it is not
    portable. First preserve the backend's order, then hydrate these IDs using
    a normal BlogPage QuerySet.
    """
    page_ids = [result.pk for result in ranked_page]
    if not page_ids:
        return []

    approved_comments_count = (
        Comment.objects.filter(page=OuterRef("pk"), is_approved=True)
        .order_by()
        .values("page")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )
    hydrated = (
        BlogPage.objects.live().public().in_site(site)
        .filter(pk__in=page_ids)
        .select_related("main_image")
        .prefetch_related("tags", "categories")
        .annotate(
            approved_comments_count=Coalesce(
                Subquery(approved_comments_count, output_field=IntegerField()), 0
            )
        )
    )
    by_id = {page.pk: page for page in hydrated}
    # A page could be unpublished between search and hydration. Do not leak it;
    # preserve the ranked order for the remaining visible results.
    return [by_id[pk] for pk in page_ids if pk in by_id]


def search(request):
    search_query = (request.GET.get("query") or "").strip()
    if len(search_query) > MAX_SEARCH_QUERY_LENGTH:
        return HttpResponseBadRequest(
            f"Search query must be {MAX_SEARCH_QUERY_LENGTH} characters or fewer."
        )

    site = Site.find_for_request(request)
    if search_query and site:
        ranked_results = (
            BlogPage.objects.live().public().in_site(site).search(search_query)
        )
    else:
        ranked_results = BlogPage.objects.none()

    paginator = Paginator(ranked_results, RESULTS_PER_PAGE)
    # Preserve the historical endpoint behaviour: malformed page values fall
    # back to the first page and values beyond the end to the last page.
    search_page = paginator.get_page(request.GET.get("page", 1))
    ranked_page = list(search_page.object_list)
    search_page.object_list = _hydrate_ranked_page(ranked_page, site) if site else []

    theme = getattr(request, "theme", "nova")
    template_name = f"blog/{theme}/search_results.html"

    return TemplateResponse(
        request,
        template_name,
        {
            "search_query": search_query,
            "search_results": search_page,
            "theme": theme,
        },
    )
