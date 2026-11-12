# vsproject/api_meta.py
# Справочные эндпоинты для publisher'а: рубрики (MPTT-дерево blog.Category)
# и теги (taggit). Чтение публичное, запись не требуется.
# На голом Wagtail без blog.Category эндпоинт категорий вернёт пустой список.
from ninja import Router

from django.http import JsonResponse

router = Router(tags=["publisher-meta"])


@router.get(
    "/categories/",
    operation_id="publisher_categories",
    summary="Рубрики сайта (id, имя, родитель, уровень)",
)
def categories(request):
    try:
        from blog.models import Category
    except ImportError:
        return {"meta": {"total_count": 0}, "items": []}

    items = []
    for cat in Category.objects.all().order_by("tree_id", "lft"):
        items.append({
            "id": cat.id,
            "name": cat.name,
            "parent_id": cat.parent_id,
            "level": cat.level,
        })
    return {"meta": {"total_count": len(items)}, "items": items}


@router.get(
    "/tags/",
    operation_id="publisher_tags",
    summary="Теги сайта (поиск для автодополнения)",
)
def tags(request, search: str = None, limit: int = 50):
    from taggit.models import Tag

    qs = Tag.objects.all().order_by("name")
    if search:
        qs = qs.filter(name__icontains=search)
    items = [
        {"id": t.id, "name": t.name}
        for t in qs[: max(1, min(limit, 200))]
    ]
    return {"meta": {"total_count": qs.count()}, "items": items}
