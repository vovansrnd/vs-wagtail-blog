"""Django integration tests for safe image URL/reference discovery.

These tests use the project's configured CustomImage and temporary media
storage. They never delete or physically move media files.
"""
from io import BytesIO
import importlib.util
import tempfile
import unittest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from PIL import Image as PILImage
from wagtail.models import Page

from blog.models import BlogPage, CustomImage

# wagtail_image_directories.references — API сканера ссылок, которого ещё нет
# в опубликованном wagtail-image-directories 0.2.1 (появится в кандидате 0.2.2).
# Отсутствие ИМЕННО этого субмодуля — штатный skip класса ниже; маскировать
# нельзя ни отсутствие самого плагина (жёсткая зависимость), ни ошибки импорта
# ВНУТРИ существующего субмодуля. Поэтому find_spec (не выполняет код
# субмодуля и не ловит его ImportError), а обычный import — только при
# наличии модуля: тогда внутренний ImportError пробрасывается как есть.
REFERENCES_API_AVAILABLE = (
    importlib.util.find_spec("wagtail_image_directories.references") is not None
)

if REFERENCES_API_AVAILABLE:
    from wagtail_image_directories.references import (
        build_image_file_target_index,
        content_field_names_for_model,
        find_referenced_images,
        scan_content_and_revision_references,
    )


@unittest.skipUnless(
    REFERENCES_API_AVAILABLE,
    "wagtail_image_directories.references отсутствует в опубликованном "
    "wagtail-image-directories 0.2.1 (API сканера ссылок — кандидат 0.2.2)",
)
class ImageReferenceScannerIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_tmp = tempfile.TemporaryDirectory(prefix="wagtail-image-refs-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_tmp.name)
        cls._media_override.enable()
        cls.addClassCleanup(cls._media_override.disable)
        cls.addClassCleanup(cls._media_tmp.cleanup)

    @classmethod
    def setUpTestData(cls):
        root = Page.get_first_root_node()
        cls.home = root.add_child(instance=Page(title="Image reference test", slug="image-ref-test"))

    def _make_image(self):
        buffer = BytesIO()
        PILImage.new("RGB", (8, 8), color=(20, 40, 60)).save(buffer, format="JPEG")
        upload = SimpleUploadedFile("reference.jpg", buffer.getvalue(), content_type="image/jpeg")
        image = CustomImage(title="Reference image", file=upload, year="2026", slug="image-reference-tests")
        image.save()
        return image

    def _make_page(self, *, body="", main_image=None, slug="image-ref-page"):
        page = self.home.add_child(instance=BlogPage(
            title="Image reference page",
            slug=slug,
            body=body,
            main_image=main_image,
        ))
        return page

    def test_current_page_html_rendition_url_is_found(self):
        image = self._make_image()
        rendition = image.get_rendition("width-1200")
        page = self._make_page(body=f'<p><img src="{rendition.url}"></p>')

        targets = build_image_file_target_index([image.pk])
        with CaptureQueriesContext(connection) as captured:
            scan = scan_content_and_revision_references(targets)

        write_sql = [
            query["sql"] for query in captured.captured_queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
        ]
        self.assertEqual(write_sql, [], "reference discovery must remain strictly read-only")
        self.assertIn(image.pk, scan["referenced_image_ids"])
        self.assertTrue(any(
            row["source"].get("object_id") == page.pk
            and row["target"].get("filter_spec") == "width-1200"
            for row in scan["matches"]
        ))

    def test_old_revision_url_is_found_after_current_body_no_longer_references_image(self):
        image = self._make_image()
        rendition = image.get_rendition("width-1200")
        page = self._make_page(body=f'<p><img src="{rendition.url}"></p>', slug="image-ref-revision")
        page.save_revision()

        page.body = "<p>Image removed from current draft.</p>"
        page.save(update_fields=["body"])

        references = find_referenced_images([image.pk])
        self.assertIn(image.pk, references)
        self.assertTrue(any(ref.get("kind") == "revision" for ref in references[image.pk]))

    def test_current_and_historical_main_image_relations_are_found(self):
        image = self._make_image()
        page = self._make_page(main_image=image, slug="image-ref-relation")
        page.save_revision()

        page.main_image = None
        page.save(update_fields=["main_image"])

        references = find_referenced_images([image.pk])
        self.assertIn(image.pk, references)
        self.assertTrue(any(ref.get("kind") == "revision_relation" for ref in references[image.pk]))

    def test_forum_posts_and_mptt_comments_are_in_current_content_field_scan(self):
        from wagtail_simple_forum.models import ForumPost
        from wagtail_mptt_comments.models import Comment

        forum_fields, forum_is_page = content_field_names_for_model(ForumPost, Page)
        comment_fields, comment_is_page = content_field_names_for_model(Comment, Page)

        self.assertFalse(forum_is_page)
        self.assertFalse(comment_is_page)
        self.assertIn("content", forum_fields)
        self.assertIn("content", comment_fields)
        self.assertIn("response", comment_fields)
