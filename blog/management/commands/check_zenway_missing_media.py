import os
import re
from collections import Counter, defaultdict

from django.conf import settings
from blog.models import BlogIndexPage, BlogPage, Comment

BLOG_INDEX_SLUG = "zw_page"
ZENWAY_PREFIX = "/media/zenway/"
ZENWAY_ROOT = os.path.join(settings.MEDIA_ROOT, "zenway")

EXT_CANDIDATES = [".jpg", ".jpeg", ".png", ".gif", ".webp"]

SRC_HREF_RE = re.compile(
    r'''(?:src|href)=["'](/media/zenway/[^"']+)["']''',
    re.IGNORECASE
)

def check_path(url_path):
    rel_path = url_path.replace(ZENWAY_PREFIX, "", 1).lstrip("/")
    full_path = os.path.join(ZENWAY_ROOT, rel_path)

    if os.path.exists(full_path):
        return {
            "exists": True,
            "full_path": full_path,
            "rel_path": rel_path,
            "alternatives": [],
        }

    base, ext = os.path.splitext(full_path)
    rel_base, rel_ext = os.path.splitext(rel_path)

    alternatives = []
    for candidate_ext in EXT_CANDIDATES:
        alt_full = base + candidate_ext
        alt_rel = rel_base + candidate_ext
        if os.path.exists(alt_full):
            alternatives.append({
                "full_path": alt_full,
                "rel_path": alt_rel,
                "ext": candidate_ext,
            })

    return {
        "exists": False,
        "full_path": full_path,
        "rel_path": rel_path,
        "alternatives": alternatives,
    }

blog_index = BlogIndexPage.objects.get(slug=BLOG_INDEX_SLUG)
pages = BlogPage.objects.child_of(blog_index)

missing_counter = Counter()
alt_ext_counter = Counter()
samples = defaultdict(list)

print("=" * 80)
print("ПРОВЕРКА ССЫЛОК НА ФАЙЛЫ /media/zenway/")
print("=" * 80)

# -------------------------
# Страницы
# -------------------------
page_count = 0
missing_refs = []

for page in pages.only("id", "title", "excerpt", "body").iterator():
    page_count += 1
    for field_name, text in [("excerpt", page.excerpt), ("body", page.body)]:
        if not text:
            continue

        for match in SRC_HREF_RE.finditer(text):
            url_path = match.group(1)
            result = check_path(url_path)

            if not result["exists"]:
                missing_counter["pages"] += 1
                missing_refs.append({
                    "kind": "page",
                    "page_id": page.id,
                    "title": page.title,
                    "field": field_name,
                    "url": url_path,
                    "rel_path": result["rel_path"],
                    "alternatives": result["alternatives"],
                })

                if result["alternatives"]:
                    for alt in result["alternatives"]:
                        alt_ext_counter[alt["ext"]] += 1

                if len(samples["pages"]) < 20:
                    samples["pages"].append({
                        "page_id": page.id,
                        "title": page.title,
                        "field": field_name,
                        "url": url_path,
                        "alternatives": result["alternatives"],
                    })

# -------------------------
# Комментарии
# -------------------------
comment_missing_refs = []

comments = Comment.objects.filter(page__in=pages).only("id", "page_id", "content")

for comment in comments.iterator():
    text = comment.content or ""
    for match in SRC_HREF_RE.finditer(text):
        url_path = match.group(1)
        result = check_path(url_path)

        if not result["exists"]:
            missing_counter["comments"] += 1
            comment_missing_refs.append({
                "kind": "comment",
                "comment_id": comment.id,
                "page_id": comment.page_id,
                "url": url_path,
                "rel_path": result["rel_path"],
                "alternatives": result["alternatives"],
            })

            if result["alternatives"]:
                for alt in result["alternatives"]:
                    alt_ext_counter[alt["ext"]] += 1

            if len(samples["comments"]) < 20:
                samples["comments"].append({
                    "comment_id": comment.id,
                    "page_id": comment.page_id,
                    "url": url_path,
                    "alternatives": result["alternatives"],
                })

print(f"Страниц проверено: {page_count}")
print(f"Битых ссылок в страницах: {missing_counter['pages']}")
print(f"Битых ссылок в комментариях: {missing_counter['comments']}")

print("\nВозможные найденные альтернативы по расширениям:")
if alt_ext_counter:
    for ext, cnt in alt_ext_counter.most_common():
        print(f"  {ext}: {cnt}")
else:
    print("  Нет")

print("\nПримеры по страницам:")
if samples["pages"]:
    for item in samples["pages"]:
        print(f"- page #{item['page_id']} «{item['title']}» [{item['field']}]")
        print(f"  missing: {item['url']}")
        if item["alternatives"]:
            for alt in item["alternatives"]:
                print(f"  alt: /media/zenway/{alt['rel_path']}")
        else:
            print("  alt: нет")
else:
    print("  Нет")

print("\nПримеры по комментариям:")
if samples["comments"]:
    for item in samples["comments"]:
        print(f"- comment #{item['comment_id']} (page #{item['page_id']})")
        print(f"  missing: {item['url']}")
        if item["alternatives"]:
            for alt in item["alternatives"]:
                print(f"  alt: /media/zenway/{alt['rel_path']}")
        else:
            print("  alt: нет")
else:
    print("  Нет")

# ------------------------------------
# Отдельно: страницы без main_image,
# но с первой картинкой в тексте
# ------------------------------------
print("\n" + "=" * 80)
print("СТРАНИЦЫ БЕЗ main_image, НО С КАРТИНКОЙ В ТЕКСТЕ")
print("=" * 80)

img_re = re.compile(r'<img[^>]+src="(/media/zenway/[^"]+)"', re.IGNORECASE)
count_no_main = 0
shown = 0

for page in pages.filter(main_image__isnull=True).only("id", "title", "excerpt", "body").iterator():
    m = img_re.search(page.excerpt or "") or img_re.search(page.body or "")
    if m:
        count_no_main += 1
        if shown < 20:
            shown += 1
            result = check_path(m.group(1))
            print(f"- page #{page.id} «{page.title}»")
            print(f"  first img: {m.group(1)}")
            if result["exists"]:
                print("  file: OK")
            elif result["alternatives"]:
                for alt in result["alternatives"]:
                    print(f"  alt: /media/zenway/{alt['rel_path']}")
            else:
                print("  alt: нет")

print(f"\nВсего таких страниц: {count_no_main}")
