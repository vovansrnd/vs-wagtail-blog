"""Д58: RSS-фид без слеша — постоянный редирект на /feed/.

Читатели старого сайта подписаны на /feed (без слеша): ~7k запросов/день
на zenway получали 404. APPEND_SLASH не срабатывает из-за catch-all
Wagtail (см. комментарий в vsproject/urls.py рядом с /reader).
"""
from django.test import TestCase


class FeedRedirectTests(TestCase):
    def test_feed_without_slash_redirects_permanently(self):
        resp = self.client.get("/feed")
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp["Location"], "/feed/")
