"""Общий безопасный санитайзер HTML для BlogPage.body/excerpt.

Разрешается форматирование редактора и iframe только от известных видеохостов
с ожидаемыми путями. Произвольные script/object/embed/style не разрешаются.
Канонические Asciinema embed-script (только https://asciinema.org/a/<id>.js)
преобразуются в безопасную preview-ссылку: запись остаётся доступной, но
сторонний JavaScript не выполняется в контексте сайта.
"""
from html import escape, unescape
import re
from urllib.parse import urlsplit

import nh3

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "s", "u", "sub", "sup", "blockquote",
    "ul", "ol", "li", "h2", "h3", "h4", "h5", "h6",
    "a", "img", "figure", "figcaption",
    "table", "thead", "tbody", "tr", "th", "td", "colgroup", "col",
    "code", "pre", "kbd", "mark", "hr", "span", "div", "details", "summary",
    "video", "source", "track", "iframe",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target", "id", "class"},
    "img": {"src", "alt", "width", "height", "loading", "class", "srcset", "sizes"},
    "td": {"colspan", "rowspan", "class", "id"},
    "th": {"colspan", "rowspan", "class", "id"},
    "tr": {"class", "id"},
    "table": {"class", "id"},
    "thead": {"class"},
    "tbody": {"class"},
    "colgroup": {"class"},
    "col": {"span", "class"},
    "code": {"class"},
    "pre": {"class"},
    "kbd": {"class"},
    "span": {"class"},
    "div": {"class"},
    "p": {"class", "id"},
    "blockquote": {"class"},
    "ul": {"class"},
    "ol": {"class"},
    "li": {"class"},
    "hr": {"class"},
    "mark": {"class"},
    "figure": {"class", "id"},
    "figcaption": {"class"},
    "h2": {"id", "class"},
    "h3": {"id", "class"},
    "h4": {"id", "class"},
    "h5": {"id", "class"},
    "h6": {"id", "class"},
    "details": {"open", "class"},
    "summary": {"class"},
    "video": {"src", "poster", "controls", "width", "height", "preload", "playsinline", "muted", "loop", "class", "id"},
    "source": {"src", "type"},
    "track": {"src", "kind", "srclang", "label", "default"},
    "iframe": {"src", "width", "height", "allow", "allowfullscreen", "frameborder", "title", "loading", "sandbox", "referrerpolicy", "class", "id"},
}

ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

# Host + path allowlist: do not turn an entire social/video domain into an iframe sandbox.
IFRAME_PATH_PREFIXES = {
    "www.youtube.com": ("/embed/",),
    "www.youtube-nocookie.com": ("/embed/",),
    "player.vimeo.com": ("/video/",),
    "vk.com": ("/video_ext.php", "/clip_ext.php"),
    "video.vk.com": ("/video_ext.php", "/clip_ext.php"),
    "rutube.ru": ("/play/embed/",),
    # Kept for legacy direct Asciinema embeds; script snippets are converted below.
    "asciinema.org": ("/a/",),
}

ALLOWED_IFRAME_ALLOW_TOKENS = {
    "autoplay", "encrypted-media", "gyroscope", "picture-in-picture",
    "fullscreen", "clipboard-write", "accelerometer", "web-share",
}

ASCIINEMA_SCRIPT_RE = re.compile(r"<script\b(?P<attrs>[^>]*)>\s*</script\s*>", re.I | re.S)
ASCIINEMA_SRC_RE = re.compile(r"\bsrc\s*=\s*([\"'])(?P<url>[^\"']+)\1", re.I)
IFRAME_OPEN_RE = re.compile(r"<iframe\b(?P<attrs>[^>]*)>", re.I)
IFRAME_ELEMENT_RE = re.compile(r"<iframe\b(?P<attrs>[^>]*)>(?:\s*</iframe\s*>)?", re.I)
IFRAME_SRC_RE = re.compile(r"\bsrc\s*=\s*([\"'])(?P<url>[^\"']+)\1", re.I)

IFRAME_SANDBOX_POLICY = "allow-scripts allow-same-origin allow-presentation"


def _iframe_src_ok(src: str) -> bool:
    try:
        parsed = urlsplit(src)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password:
        return False
    if port not in (None, 443):
        return False
    prefixes = IFRAME_PATH_PREFIXES.get(host, ())
    return any(
        parsed.path.startswith(prefix) if prefix.endswith("/") else parsed.path == prefix
        for prefix in prefixes
    )


def _attribute_filter(tag: str, attr: str, value: str):
    if tag == "a" and attr == "target":
        return value if value.lower() in {"_blank", "_self"} else None

    if attr == "id" and tag in {"a", "p", "div", "figure", "table", "tr", "td", "th", "h2", "h3", "h4", "h5", "h6", "iframe", "video"}:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            return None

    if tag in {"video", "source", "track"} and attr in {"src", "poster"}:
        # Permit local media URLs and HTTPS media URLs only. Protocol-relative,
        # data:, javascript:, and non-HTTPS external media are rejected.
        if value.startswith("/") and not value.startswith("//"):
            if "\\" in value or any(part == ".." for part in value.split("/")):
                return None
            return value
        try:
            parsed_media = urlsplit(value)
        except ValueError:
            return None
        if parsed_media.scheme.lower() != "https" or not parsed_media.hostname or parsed_media.username or parsed_media.password:
            return None
        if parsed_media.port not in (None, 443):
            return None
        return value
    if tag == "source" and attr == "type":
        return value if re.fullmatch(r"(?:video|audio)/[A-Za-z0-9.+-]{1,64}", value, re.I) else None
    if tag == "track" and attr == "kind":
        return value if value in {"subtitles", "captions", "descriptions", "chapters", "metadata"} else None
    if tag == "track" and attr == "srclang":
        return value if re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value) else None
    if tag == "video" and attr == "preload":
        return value if value in {"none", "metadata", "auto"} else None
    if tag in {"video", "iframe"} and attr in {"width", "height"}:
        if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 4096:
            return None
        return value

    if tag != "iframe":
        return value

    if attr == "src":
        candidate = "https:" + value if value.startswith("//") else value
        return candidate if _iframe_src_ok(candidate) else None
    if attr == "allow":
        tokens = [part.strip().lower() for part in re.split(r"[;,]", value)]
        kept = [token for token in tokens if token in ALLOWED_IFRAME_ALLOW_TOKENS]
        return "; ".join(dict.fromkeys(kept)) or None
    if attr == "sandbox":
        # Provider scripts are needed for playback. Keep the iframe on its
        # cross-origin provider origin and deny forms, popups and top navigation.
        return "allow-scripts allow-same-origin allow-presentation"
    if attr == "referrerpolicy":
        return value if value in {"no-referrer", "strict-origin-when-cross-origin"} else "strict-origin-when-cross-origin"
    if attr == "frameborder":
        return "0" if value == "0" else None
    if attr == "loading":
        return value if value.lower() in {"lazy", "eager"} else None
    if attr == "allowfullscreen":
        return ""
    # Inline style on third-party frames is unnecessary and can carry unsafe CSS.
    if attr == "style":
        return None
    return value


def _replace_asciinema_scripts(html: str) -> str:
    """Convert only the official Asciinema script URL to a safe clickable preview."""
    def replace(match: re.Match) -> str:
        src_match = ASCIINEMA_SRC_RE.search(match.group("attrs"))
        if not src_match:
            return match.group(0)
        url = unescape(src_match.group("url")).strip()
        try:
            parsed = urlsplit(url)
        except ValueError:
            return match.group(0)
        id_match = re.fullmatch(r"/a/([A-Za-z0-9_-]{1,80})\.js", parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "asciinema.org"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not id_match
        ):
            return match.group(0)
        cast_id = id_match.group(1)
        page_url = f"https://asciinema.org/a/{cast_id}"
        preview_url = f"{page_url}.svg"
        return (
            f'<a href="{escape(page_url, quote=True)}" target="_blank">'
            f'<img src="{escape(preview_url, quote=True)}" alt="Asciinema recording" '
            f'loading="lazy"></a>'
        )

    return ASCIINEMA_SCRIPT_RE.sub(replace, html)


def _drop_disallowed_iframes(html: str) -> str:
    """Удалить элемент iframe целиком, если src не входит в allowlist.

    Д66-Б: прежний атрибутный фильтр удалял только src, оставляя «пустой»
    <iframe>; контракт тестов требует удаления элемента целиком.
    """
    def drop(match: re.Match) -> str:
        src = IFRAME_SRC_RE.search(match.group("attrs"))
        if not src:
            return ""
        url = src.group("url")
        candidate = "https:" + url if url.startswith("//") else url
        return match.group(0) if _iframe_src_ok(candidate) else ""

    return IFRAME_ELEMENT_RE.sub(drop, html)



def _add_iframe_defaults(html: str) -> str:
    """Ensure every allowlisted embed gets a restrictive sandbox and referrer policy."""
    def add_defaults(match: re.Match) -> str:
        attrs = match.group("attrs")
        if not re.search(r"\bsandbox\s*=", attrs, re.I):
            attrs += f' sandbox="{IFRAME_SANDBOX_POLICY}"'
        if not re.search(r"\breferrerpolicy\s*=", attrs, re.I):
            attrs += ' referrerpolicy="strict-origin-when-cross-origin"'
        return f"<iframe{attrs}>"

    return IFRAME_OPEN_RE.sub(add_defaults, html)


def sanitize_html(html: str) -> str:
    """Clean HTML while preserving supported editor markup and safe embeds."""
    if not html:
        return html or ""
    prepared = _replace_asciinema_scripts(str(html))
    prepared = _drop_disallowed_iframes(prepared)
    prepared = _add_iframe_defaults(prepared)
    return nh3.clean(
        prepared,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_attribute_filter,
        url_schemes=ALLOWED_URL_SCHEMES,
        # Do not add nofollow: internal editorial links must remain crawlable.
        # noopener/noreferrer prevent opener attacks and referrer leakage for links.
        link_rel="noopener noreferrer",
    )
