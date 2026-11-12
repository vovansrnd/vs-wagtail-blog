"""Тесты санитизации HTML (Этап 4) — контракт разрешённой разметки."""
from django.test import TestCase

from blog.html_sanitizer import sanitize_html
from blog.models import BlogPage


class SanitizerContractTests(TestCase):
    def test_script_removed(self):
        out = sanitize_html("<p>ok</p><script>alert(1)</script>")
        self.assertNotIn("<script", out)
        self.assertIn("<p>ok</p>", out)

    def test_event_handlers_removed(self):
        out = sanitize_html('<img src="/x.png" onerror="alert(1)"><p onclick="x()">t</p>')
        self.assertNotIn("onerror", out)
        self.assertNotIn("onclick", out)
        self.assertIn('<img src="/x.png"', out)

    def test_javascript_url_removed(self):
        out = sanitize_html('<a href="javascript:alert(1)">link</a>')
        self.assertNotIn("javascript:", out)

    def test_allowed_markup_preserved(self):
        html = ('<h2>Заголовок</h2><p class="lead">Текст <strong>жирный</strong> и '
                '<em>курсив</em>.</p><ul><li>пункт</li></ul>'
                '<table><tr><td colspan="2">ячейка</td></tr></table>'
                '<pre class="line-numbers"><code>x = 1</code></pre>'
                '<a href="https://example.com" title="t">ссылка</a>'
                '<img src="/media/images/a.jpg" alt="a" loading="lazy">')
        out = sanitize_html(html)
        for fragment in ("<h2", "<strong>", "<em>", "<ul><li>", 'colspan="2"',
                         '<pre class="line-numbers">', "<code>", "https://example.com",
                         'loading="lazy"'):
            self.assertIn(fragment, out)
        self.assertNotIn("nofollow", out)

    def test_headings_keyboard_and_details_markup_preserved(self):
        out = sanitize_html(
            '<h5 id="section-five">H5</h5><h6 id="section-six">H6</h6>'
            '<p>Нажмите <kbd>Ctrl</kbd>.</p><details open><summary>Подробности</summary>Текст</details>'
        )
        self.assertIn('<h5 id="section-five">', out)
        self.assertIn('<h6 id="section-six">', out)
        self.assertIn('<kbd>Ctrl</kbd>', out)
        # nh3 канонизирует булев атрибут в open=""
        self.assertIn('<details open=""', out)
        self.assertIn('<summary>', out)

    def test_disallowed_tags_dropped(self):
        out = sanitize_html('<iframe src="https://evil"></iframe><style>body{}</style><p>t</p>')
        self.assertNotIn("<iframe", out)
        self.assertNotIn("<style", out)
        self.assertIn("<p>t</p>", out)

    def test_empty_passthrough(self):
        self.assertEqual(sanitize_html(""), "")
        self.assertEqual(sanitize_html(None), "")

    @classmethod
    def setUpTestData(cls):
        from wagtail.models import Page
        root = Page.get_first_root_node()
        cls.parent = root.add_child(instance=Page(title="San parent", slug="san-parent"))

    def test_blogpage_save_sanitizes_body(self):
        page = BlogPage(title="t", slug="san-test", body="<p>ok</p><script>x()</script>")
        self.parent.add_child(instance=page)
        page.refresh_from_db()
        self.assertNotIn("<script", page.body)
        self.assertIn("<p>ok</p>", page.body)

    def test_blogpage_save_sanitizes_excerpt(self):
        page = BlogPage(title="t2", slug="san-test-2", excerpt='<strong>e</strong><script>y()</script>')
        self.parent.add_child(instance=page)
        page.refresh_from_db()
        self.assertIn("<strong>e</strong>", page.excerpt)
        self.assertNotIn("<script", page.excerpt)


class IframeOriginPolicyTests(TestCase):
    """Этап 5 ТЗ: iframe только с доверенных origin; script удаляется всегда."""

    def test_youtube_embed_preserved(self):
        out = sanitize_html(
            '<iframe src="https://www.youtube.com/embed/XyZ" width="560" height="315" '
            'allow="autoplay; encrypted-media" allowfullscreen></iframe>')
        self.assertIn("youtube.com/embed", out)
        self.assertIn("allowfullscreen", out)
        self.assertIn('sandbox="allow-scripts allow-same-origin allow-presentation"', out)
        self.assertIn('referrerpolicy="strict-origin-when-cross-origin"', out)

    def test_sandbox_and_referrer_policy_are_added_to_legacy_iframes(self):
        out = sanitize_html('<iframe src="https://www.youtube.com/embed/abc"></iframe>')
        self.assertIn('sandbox="allow-scripts allow-same-origin allow-presentation"', out)
        self.assertIn('referrerpolicy="strict-origin-when-cross-origin"', out)

    def test_vk_video_and_clip_embeds_preserved_but_other_paths_rejected(self):
        video = sanitize_html('<iframe src="//vk.com/video_ext.php?oid=1&amp;id=2"></iframe>')
        clip = sanitize_html('<iframe src="https://vk.com/clip_ext.php?oid=1&amp;id=2"></iframe>')
        self.assertIn("vk.com/video_ext", video)
        self.assertIn("vk.com/clip_ext", clip)
        denied = sanitize_html('<iframe src="https://vk.com/id1"></iframe>')
        self.assertNotIn("<iframe", denied)

    def test_vimeo_and_rutube_preserved(self):
        self.assertIn("player.vimeo.com", sanitize_html('<iframe src="https://player.vimeo.com/video/1"></iframe>'))
        self.assertIn("rutube.ru", sanitize_html('<iframe src="https://rutube.ru/play/embed/1"></iframe>'))

    def test_unknown_origin_removed(self):
        out = sanitize_html('<iframe src="https://evil.example.com/frame"></iframe>')
        self.assertNotIn("evil.example.com", out)
        self.assertNotIn("<iframe", out)

    def test_javascript_and_relative_src_removed(self):
        out = sanitize_html('<iframe src="javascript:alert(1)"></iframe><iframe src="/local/frame"></iframe>')
        self.assertNotIn("<iframe", out)

    def test_script_always_removed(self):
        out = sanitize_html('<script>alert(1)</script><p>текст</p>')
        self.assertNotIn("<script", out)
        self.assertIn("текст", out)

    def test_official_asciinema_script_becomes_safe_preview_link(self):
        out = sanitize_html(
            '<script src="https://asciinema.org/a/abc123.js" '
            'id="asciicast-abc123" async></script>'
        )
        self.assertNotIn('<script', out)
        self.assertIn('href="https://asciinema.org/a/abc123"', out)
        self.assertIn('src="https://asciinema.org/a/abc123.svg"', out)
        self.assertIn('rel="noopener noreferrer"', out)

    def test_untrusted_script_is_not_converted(self):
        out = sanitize_html('<script src="https://evil.example/x.js"></script><p>ok</p>')
        self.assertNotIn('<script', out)
        self.assertNotIn('evil.example', out)
        self.assertIn('<p>ok</p>', out)

    def test_iframe_allow_tokens_and_paths_are_restricted(self):
        out = sanitize_html(
            '<iframe src="https://vk.com/video_ext.php?id=1" '
            'allow="autoplay; clipboard-write; camera; geolocation" '
            'style="position:fixed" width="640"></iframe>'
        )
        self.assertIn('allow="autoplay; clipboard-write"', out)
        self.assertNotIn('camera', out)
        self.assertNotIn('geolocation', out)
        self.assertNotIn('style=', out)
        bad_path = sanitize_html('<iframe src="https://vk.com/evil/path"></iframe>')
        self.assertNotIn('<iframe', bad_path)

    def test_video_markup_is_preserved_with_safe_sources(self):
        out = sanitize_html(
            '<video controls width="640" preload="metadata" poster="/media/poster.jpg">'
            '<source src="/media/video.mp4" type="video/mp4">'
            '<track src="/media/captions.vtt" kind="captions" srclang="ru" label="Русский">'
            '</video>'
        )
        self.assertIn('<video', out)
        self.assertIn('controls', out)
        self.assertIn('/media/video.mp4', out)
        self.assertIn('kind="captions"', out)
        self.assertIn('srclang="ru"', out)

    def test_video_rejects_data_protocol_relative_and_http_external_sources(self):
        out = sanitize_html(
            '<video src="data:text/html,<script>alert(1)</script>"></video>'
            '<video src="//evil.example/video.mp4"></video>'
            '<video src="http://evil.example/video.mp4"></video>'
        )
        self.assertNotIn('data:text/html', out)
        self.assertNotIn('//evil.example', out)
        self.assertNotIn('http://evil.example', out)

    def test_iframe_vk_path_must_match_exact_endpoint(self):
        out = sanitize_html('<iframe src="https://vk.com/video_ext.php.evil"></iframe>')
        self.assertNotIn('<iframe', out)

    def test_double_use_content_preserved(self):
        """Реальный контент: текст вокруг embeds не теряется."""
        out = sanitize_html('<p>до</p><iframe src="https://www.youtube.com/embed/a"></iframe><p>после</p>')
        self.assertIn("до", out)
        self.assertIn("youtube", out)
        self.assertIn("после", out)
