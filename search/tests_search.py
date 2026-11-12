from types import SimpleNamespace

from django.test import RequestFactory, TestCase
from django.utils import timezone
from wagtail.models import Page, Site

from blog.models import BlogPage
from search.views import MAX_SEARCH_QUERY_LENGTH, _hydrate_ranked_page, search


class SearchQueryLimitTests(TestCase):
    def test_long_search_query_is_rejected_before_search_backend(self):
        request = RequestFactory().get(
            "/search/", {"query": "x" * (MAX_SEARCH_QUERY_LENGTH + 1)},
            HTTP_HOST="search.test",
        )
        response = search(request)
        self.assertEqual(response.status_code, 400)


class SearchResultHydrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        root = Page.get_first_root_node()
        cls.site = Site.objects.create(
            hostname="hydration.test", port=80, root_page=root,
            site_name="Hydration tests", is_default_site=False,
        )
        cls.pages = []
        for slug in ("ranked-second", "ranked-first"):
            page = root.add_child(instance=BlogPage(
                title=slug, slug=slug, date_published=timezone.now(), body=""
            ))
            page.save_revision().publish()
            cls.pages.append(page)

    def test_hydration_preserves_search_order_and_prefetches_display_data(self):
        ranked = [SimpleNamespace(pk=self.pages[1].pk), SimpleNamespace(pk=self.pages[0].pk)]
        hydrated = _hydrate_ranked_page(ranked, self.site)

        self.assertEqual([page.pk for page in hydrated], [self.pages[1].pk, self.pages[0].pk])
        for page in hydrated:
            self.assertIn("main_image", page._state.fields_cache)
            self.assertIn("tags", page._prefetched_objects_cache)
            self.assertIn("categories", page._prefetched_objects_cache)
            self.assertEqual(page.approved_comments_count, 0)

    def test_empty_ranked_page_returns_empty_list(self):
        self.assertEqual(_hydrate_ranked_page([], self.site), [])
