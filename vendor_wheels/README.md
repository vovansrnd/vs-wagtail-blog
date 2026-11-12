# Контролируемые wheel-артефакты (НЕ на PyPI)

Эти пакеты отсутствуют на PyPI в данных версиях. При создании нового
окружения или переустановке зависимостей `pip install -r requirements*`
получит с PyPI более СТАРЫЕ версии (prose-editor-images 0.2.3 без кнопки
вставки изображений; mptt-comments 0.2.7 без comments/form/ — сломает
ленивую форму комментариев). Поэтому колёса хранятся здесь и ставятся
ЯВНО после базовой установки:

    pip install --no-deps vendor_wheels/*.whl

| Пакет | Версия | Источник | Отличие от PyPI |
|---|---|---|---|
| wagtail_prose_editor_images | 0.2.4 | плагин-репо, коммит 4803255 | кнопка вставки изображений в Prose Editor работает; per-field условие; устойчивые сканы |
| wagtail_mptt_comments | 0.2.8 | плагин-репо, коммит 9c4bff4 | endpoint comments/form/<id>/ (ленивая форма) + фикс редиректа после POST |
| wagtail_simple_forum | 0.1.2 | плагин-репо, коммит 0468f75 (0.1.1 на PyPI без фиксов eb2f670) | PM display, hidden fields, temp token, правки закрытых тем |
| wagtail_image_directories | 0.2.1 | плагин-репо, коммит e147100 (0.2.0 на PyPI без фикса 5170ece) | lazy image permission policy без deprecated wagtail.images.permissions уровня модуля |

На проде установлены именно эти версии (2026-11-05).
Публикация на PyPI — отдельное решение владельца (после публикации этот
каталог можно удалить).
