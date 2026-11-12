# Changelog — core follow-up candidate

## 2026-10-30 — LangReader (языковая читалка)

- Владелец может отдельно включить приватный плагин wagtail-langreader 0.3.x на `/reader/`: EPUB/TXT/HTML-книги,
  личный словарь (learning/known/ignored), повторение по Лейтнеру, локальные
  словари (En-Ru, Es-Ru DSL) и словарная озвучка `media/langreader/sounds/<lang>.pak + .idx`.
- Интерфейс принудительно на русском: локаль `locale/ru/` в плагине,
  `LANGREADER_UI_LANGUAGE = "ru"` + middleware `wagtail_langreader.middleware.reader_language`
  (в MIDDLEWARE после LocaleMiddleware). Переключателя в UI нет — язык ридера задаётся
  настройкой; `None` возвращает стандартное поведение Django (язык браузера).
- JS-словарь читалки выбирается по `LANGUAGE_CODE` страницы: в проект добавлен
  контекст-процессор `django.template.context_processors.i18n`.
- Мануал читалки переведён через i18n (help.html, _dict_guide.html → blocktrans).
- `media/langreader/` (книги, словари, озвучка, обложки, ассеты) не хранится в git.

## 2026-10-29 — follow-up audit

- Fixed SearchPage hydration after Wagtail `SearchResults`, preserving ranking and adding preview annotations only for the current page.
- Prevented theme/palette clean-URL redirects from converting POST submissions to GET.
- Replaced the public contact form's client-controlled timestamp with a signed, age-limited nonce; the page returns no-store headers.
- Enforced safe `rel` attributes on comment links, annotated comment counts on tag/category/archive previews, and improved RSS enclosure-size handling.
- Disabled Gravatar downloads by default and configured local avatar fallbacks for the forum.
- Made Open Graph and Floppy article image sizes real renditions while preserving the explicitly bounded legacy rendition allowlist elsewhere.
- Added staged schema changes for image path length and Unicode folder slugs; existing media files are not moved.

## Public distribution notes

- The public core does not include the separate HexIsles game application.
- The private LangReader integration is optional and disabled by default; set
  `ENABLE_LANGREADER=1` only when its package and data are installed separately.
- `requirements-local.txt` pins stable Wagtail plugin releases available from PyPI.
  Unreleased D67 candidates are not installation requirements.

