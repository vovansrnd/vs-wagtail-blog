# blog/templatetags/blog_filters.py

from django import template

register = template.Library()


@register.filter
def multiply(value, arg):
    """Фильтр для умножения. Использование: {{ value|multiply:arg }}"""
    try:
        return int(value) * int(arg)
    except (ValueError, TypeError):
        return ''


@register.filter
def filter_approved(queryset):
    return queryset.filter(is_approved=True)


@register.filter
def filter_root(queryset):
    return queryset.filter(parent__isnull=True)
