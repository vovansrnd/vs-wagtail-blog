# blog/templatetags/prose_editor_tags.py
from django import template
from django.templatetags.static import static
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def prose_editor_importmap():
    importmap = f"""
<script type="importmap">
{{
    "imports": {{
        "django-prose-editor/editor": "{static('django_prose_editor/editor.js')}",
        "django-prose-editor/configurable": "{static('django_prose_editor/configurable.js')}"
    }}
}}
</script>
    """
    return mark_safe(importmap)
