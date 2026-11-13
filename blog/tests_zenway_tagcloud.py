"""Д68: регрессионные тесты fix/zenway-single-tag-cloud.

Дефект 1 (два одинаковых облака): zenway/base.html вызывал
get_tags_for_cloud дважды ('tech' и 'life'), но templatetag применяет
фильтр секции только если страница-раздел life существует в сайте
(blog_tags.py: нет life_index — фильтрации нет вовсе). Оба вызова
получали один и тот же набор статей, облака выходили одинаковыми.

Дефект 2 (заголовок): компонент tags_cloud.html игнорировал переданный
title — рабочая строка {{ title|default:"Облако тегов" }} была обёрнута
в {% comment %}, вместо неё выводился жёсткий текст «Облако тегов».

Решение координатора (fix/zenway-single-tag-cloud): ZenWay показывает
ОДНО общее облако «Популярные теги»; компонент снова использует
переданный title. Несуществующее для ZenWay деление на «технику» и
«жизнь» не вводится.

Теги в тестах — ASCII, чтобы не зависеть от политики slug-ификации
кириллицы (templatetag исключает теги с пустым slug).
"""
from pathlib import Path
from types import SimpleNamespace

import django.apps
from django.template import Context, RequestContext, Template
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone
from wagtail.models import Page, Site

from blog.models import BlogPage

BLOG_APP_PATH = Path(django.apps.apps.get_app_config("blog").path)
ZENWAY_BASE = BLOG_APP_PATH / "templates" / "blog" / "zenway" / "base.html"

# Точная копия исправленного блока zenway/base.html: один вызов templatetag,
# один include компонента с переданным title.
MIMIC_TEMPLATE = Template(
    "{% load blog_tags %}"
    '{% get_tags_for_cloud "tech" as popular_tags %}'
    "{% if popular_tags %}"
    '{% include "blog/zenway/components/tags_cloud.html"'
    ' with tags=popular_tags title="Популярные теги" %}'
    "{% endif %}"
)

FAKE_TAG = SimpleNamespace(slug="electronics", name="electronics", weight=2, num_times=3)


class TagsCloudComponentTitleTests(SimpleTestCase):
    """Компонент выводит ПЕРЕДАННЫЙ title; без title — дефолт «Облако тегов»."""

    def _render(self, context):
        return Template(
            '{% include "blog/zenway/components/tags_cloud.html" %}'
        ).render(RequestContext(RequestFactory().get("/"), context))

    def test_component_uses_passed_title(self):
        html = self._render({"title": "Популярные теги", "tags": [FAKE_TAG]})
        self.assertIn("Популярные теги", html)
        self.assertNotIn("Облако тегов", html)

    def test_component_default_title_without_title(self):
        html = self._render({"tags": [FAKE_TAG]})
        self.assertIn("Облако тегов", html)

    def test_component_renders_tag_links(self):
        html = self._render({"title": "Популярные теги", "tags": [FAKE_TAG]})
        self.assertIn('class="widget tag-cloud"', html)
        self.assertIn("/tag/electronics", html)


class ZenWayBaseTemplateStructureTests(SimpleTestCase):
    """В zenway/base.html ровно ОДНО облако и title передаётся компоненту.

    Регрессия на первый дефект: второй вызов get_tags_for_cloud (секция
    'life') не должен вернуться в шаблон, пока templatetag без страницы
    life не разделяет наборы."""

    def test_exactly_one_cloud_call(self):
        src = ZENWAY_BASE.read_text(encoding="utf-8")
        self.assertEqual(src.count("get_tags_for_cloud"), 1)

    def test_title_passed_to_component(self):
        src = ZENWAY_BASE.read_text(encoding="utf-8")
        self.assertIn('title="Популярные теги"', src)


class ZenWayTagCloudFunctionalTests(TestCase):
    """E2E-мимикрия исправленного блока zenway/base.html на реальных
    данных: ровно один блок облака, переданный заголовок, ссылки тегов.

    И документирование корня исходного дефекта: без страницы-раздела life
    templatetag возвращает ОДИН И ТОТ ЖЕ набор для 'tech' и 'life' —
    поэтому прежние два облака были одинаковыми."""

    @classmethod
    def setUpTestData(cls):
        cls.root = Page.get_first_root_node()
        cls.site = Site.objects.create(
            hostname="zenway-tagcloud.test", port=80, root_page=cls.root,
            site_name="ZenWay tagcloud", is_default_site=False,
        )
        for title, slug, tag_name in (
            ("Статья про паяльник", "solder", "electronics"),
            ("Статья про светодиоды", "leds", "electronics"),
            ("Заметка о походе", "hike", "hiking"),
        ):
            page = cls.root.add_child(instance=BlogPage(
                title=title, slug=slug, date_published=timezone.now(), body="",
            ))
            # modelcluster откладывает запись строк связи до save()
            # (проверено: без save() создаются Tag, но не BlogTag).
            page.tags.add(tag_name)
            page.save()

    def _request(self):
        return RequestFactory().get("/", HTTP_HOST="zenway-tagcloud.test")

    def test_single_cloud_rendered_with_passed_title(self):
        html = MIMIC_TEMPLATE.render(RequestContext(self._request(), {}))
        self.assertEqual(html.count('class="widget tag-cloud"'), 1)
        self.assertIn("Популярные теги", html)
        self.assertNotIn("Облако тегов", html)
        self.assertIn("/tag/electronics", html)
        self.assertIn("/tag/hiking", html)

    def test_without_life_page_both_sections_return_same_set(self):
        """Корень дефекта: нет страницы life — фильтр секции не применяется,
        наборы 'tech' и 'life' идентичны. ZenWay не полагается на секции."""
        from blog.templatetags.blog_tags import get_tags_for_cloud

        # Прямой вызов функции тега: RequestContext связывает процессоры
        # (и ключ request) только при рендеринге, поэтому вне шаблона
        # передаём request через обычный Context — как это видно тегу
        # внутри реального рендера.
        ctx = Context({"request": self._request()})
        tech = get_tags_for_cloud(ctx, "tech")
        life = get_tags_for_cloud(ctx, "life")
        self.assertTrue(tech, "облако не должно быть пустым")
        self.assertEqual(
            [(t.slug, t.num_times) for t in tech],
            [(t.slug, t.num_times) for t in life],
        )
