from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from wagtail.models import Page, Site

from wagtail.search.index import SearchField

from blog.models import BlogIndexPage, BlogPage


class SearchIndexDefinitionTests(TestCase):
    def test_title_is_inherited_once_from_wagtail_page(self):
        # Дубликат — повторное объявление SearchField("title"):
        # AutocompleteField/FilterField из Page.search_fields штатны.
        title_fields = [
            field for field in BlogPage.search_fields
            if isinstance(field, SearchField) and field.field_name == "title"
        ]
        self.assertEqual(len(title_fields), 1)


class BlogNavigationTieBreakTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        root = Page.get_first_root_node()
        Site.objects.create(
            hostname="navigation.test", port=80, root_page=root,
            site_name="Navigation tests", is_default_site=False,
        )
        moment = timezone.now().replace(microsecond=0)
        cls.earlier = root.add_child(instance=BlogPage(
            title="Earlier", slug="nav-earlier", date_published=moment - timedelta(days=1), body=""
        ))
        cls.first_same_date = root.add_child(instance=BlogPage(
            title="Same A", slug="nav-same-a", date_published=moment, body=""
        ))
        cls.second_same_date = root.add_child(instance=BlogPage(
            title="Same B", slug="nav-same-b", date_published=moment, body=""
        ))
        cls.later = root.add_child(instance=BlogPage(
            title="Later", slug="nav-later", date_published=moment + timedelta(days=1), body=""
        ))

    def test_same_timestamp_pages_have_deterministic_previous_and_next(self):
        self.assertEqual(self.second_same_date.get_prev_post().pk, self.first_same_date.pk)
        self.assertEqual(self.first_same_date.get_next_post().pk, self.second_same_date.pk)

    def test_different_timestamp_pages_keep_chronological_order(self):
        self.assertEqual(self.first_same_date.get_prev_post().pk, self.earlier.pk)
        self.assertEqual(self.second_same_date.get_next_post().pk, self.later.pk)

class BlogIndexPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        root = Page.get_first_root_node()
        cls.site = Site.objects.create(
            hostname="blog-index.test", port=80, root_page=root,
            site_name="Blog index tests", is_default_site=False,
        )
        cls.index = root.add_child(instance=BlogIndexPage(
            title="Blog index", slug="blog-index-tests"
        ))
        for number in range(7):
            post = cls.index.add_child(instance=BlogPage(
                title=f"Pagination post {number}",
                slug=f"pagination-post-{number}",
                date_published=timezone.now() - timedelta(minutes=number),
                body="",
            ))
            post.save_revision().publish()

    def _context(self, page=None):
        from django.test import RequestFactory
        request = RequestFactory().get(
            "/blog-index-tests/" if page is None else f"/blog-index-tests/?page={page}",
            HTTP_HOST="blog-index.test",
        )
        return self.index.get_context(request)

    def test_valid_pages_return_the_expected_slice(self):
        self.assertEqual(len(self._context()["blogs"].object_list), 6)
        self.assertEqual(len(self._context("2")["blogs"].object_list), 1)

    def test_invalid_and_out_of_range_pages_return_404(self):
        from django.http import Http404
        for value in ("0", "3", "999999", "not-a-number"):
            with self.subTest(page=value), self.assertRaises(Http404):
                self._context(value)
