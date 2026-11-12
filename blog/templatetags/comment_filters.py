# blog/templatetags/comment_filters.py
import bleach
from bleach.linkifier import Linker
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _force_comment_link_rel(attrs, new=False):
    # Comment links are user-generated, even when the surrounding HTML was
    # stored years ago. Never trust a submitted rel="opener" or DoFollow value.
    if attrs.get((None, "href")):
        attrs[(None, "rel")] = "nofollow ugc noopener noreferrer"
    return attrs


_comment_linker = Linker(
    callbacks=[_force_comment_link_rel],
    skip_tags={"pre", "code"},
    parse_email=False,
)


@register.filter(is_safe=True)
def format_comment_content(value):
    if not value:
        return ""

    raw_html = str(value)

    # Список тегов, которые разрешено выводить в комментариях
    allowed_tags = [
        'p', 'br', 'b', 'strong', 'i', 'em', 'u', 'strike',
        'a', 'blockquote', 'ol', 'ul', 'li', 'code', 'pre'
    ]

    # Разрешенные атрибуты
    allowed_attrs = {
        'a': ['href', 'title', 'target', 'rel'],
    }

    # Очищаем HTML от опасных тегов (скриптов, iframe и т.д.)
    cleaned_html = bleach.clean(
        raw_html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        strip=True
    )

    # Linker also normalizes rel on pre-existing <a> elements. It may turn bare
    # URLs into links; that is acceptable for comment content, and all generated
    # links receive the same safe rel policy.
    return mark_safe(_comment_linker.linkify(cleaned_html))
