"""Utilities for safe, non-duplicating image markup in rendered article HTML."""
import re

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTR_VALUE = r'(?P<value>"[^"]*"|\'[^\']*\'|[^\s>]+)'
CLASS_ATTR_RE = re.compile(r"(?<![\w:-])class\s*=\s*" + _ATTR_VALUE, re.IGNORECASE)
LOADING_ATTR_RE = re.compile(r"(?<![\w:-])loading\s*=\s*" + _ATTR_VALUE, re.IGNORECASE)


def _insert_attr(tag: str, name: str, value: str) -> str:
    return re.sub(
        r"\s*/?>$",
        lambda match: f' {name}="{value}"{match.group(0)}',
        tag,
    )


def _collapse_attr(tag: str, attr_re: re.Pattern, name: str, value: str) -> str:
    """Write exactly one canonical attribute, preserving unrelated markup."""
    matches = list(attr_re.finditer(tag))
    if not matches:
        return _insert_attr(tag, name, value)

    first = matches[0]
    # Remove later duplicates from right to left so earlier offsets stay valid.
    for duplicate in reversed(matches[1:]):
        tag = tag[:duplicate.start()] + tag[duplicate.end():]
    first = attr_re.search(tag)
    if first is None:  # Defensive: the first match must survive duplicate removal.
        return _insert_attr(tag, name, value)
    return tag[:first.start()] + f'{name}="{value}"' + tag[first.end():]


def _decorate_img_tag(tag: str, *, is_first: bool) -> str:
    class_matches = list(CLASS_ATTR_RE.finditer(tag))
    class_tokens = []
    for match in class_matches:
        raw = match.group("value").strip().strip('"\'')
        for token in raw.split():
            if token not in class_tokens:
                class_tokens.append(token)
    if "content-image" not in class_tokens:
        class_tokens.append("content-image")
    tag = _collapse_attr(tag, CLASS_ATTR_RE, "class", " ".join(class_tokens))

    loading_matches = list(LOADING_ATTR_RE.finditer(tag))
    if is_first:
        # The lead image must not be lazy-loaded; preserve explicit eager/auto.
        current = loading_matches[0].group("value").strip('"\' ').lower() if loading_matches else ""
        loading = "eager" if not current or current == "lazy" else current
    else:
        # Respect an explicit author choice; add lazy only when the attribute is absent.
        loading = (
            loading_matches[0].group("value").strip('"\' ')
            if loading_matches else "lazy"
        )
    return _collapse_attr(tag, LOADING_ATTR_RE, "loading", loading)


def optimize_content_images(content):
    """Add a content class once; eager-load the lead image, lazy-load later ones."""
    if not content:
        return ""
    count = 0

    def replace(match):
        nonlocal count
        is_first = count == 0
        count += 1
        return _decorate_img_tag(match.group(0), is_first=is_first)

    return IMG_TAG_RE.sub(replace, str(content))
