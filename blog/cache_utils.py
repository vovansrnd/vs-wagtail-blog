"""Маркер эпохи инвалидации блога (singleton row в основной БД).

Не кэш: не вытесняется, переживает restart, cache.clear() и вытеснение
любого cache backend. Чтение — один SELECT по pk. Запись — атомарный
UPDATE через InvalidationEpoch.bump(). Миграция 0037 создаёт таблицу.

Модель InvalidationEpoch определена в blog.models (импортируем лениво
во избежание circular import через signals).
"""


# Контекстно-изолированный кэш эпохи: рендер запрашивает её 5-7 раз
# (page cache key, компоненты шаблонов). Эпоха не меняется в течение одного
# HTTP-запроса (bump происходит в других запросах), поэтому кэшируем до
# сигнала request_started.
#
# ИЗОЛЯЦИЯ: asgiref.local.Local хранит значение отдельно на поток/async-контекст
# (в async использует contextvars). Глобальная переменная процесса была бы
# конкурентным дефектом: reset из запроса B стирал бы значение запроса A,
# и A получал бы эпоху B (зависимость одного посетителя от другого).
# bump() сбрасывает кэш ТОЛЬКО текущего контекста — параллельные запросы
# сохраняют своё согласованное значение до конца ответа.
from contextvars import ContextVar

# ContextVar вместо asgiref.local.Local: семантика изоляции гарантирована
# языком. В WSGI — по потоку; в ASGI — по контексту задачи (set в задаче B
# не виден задаче A: контекст копируется при создании задачи). asgiref.local
# (не thread_critical) в asyncio хранит общий мутируемый dict — reset в
# задаче B затирал кэш задачи A (подтверждено тестом EpochContextIsolation).
_epoch_var = ContextVar("blog_cache_epoch", default=None)


def _reset_epoch_cache(**kwargs):
    _epoch_var.set(None)


from django.core.signals import request_started as _request_started
_request_started.connect(_reset_epoch_cache)


def get_blog_cache_epoch():
    """Вернуть текущую эпоху; отсутствие строки безопасно."""
    value = _epoch_var.get()
    if value is None:
        from .models import InvalidationEpoch
        value = InvalidationEpoch.get_epoch()
        _epoch_var.set(value)
    return value


def bump_blog_cache_epoch():
    """Сменить эпоху и сбросить кэш текущего контекста (параллельные
    запросы сохраняют своё значение до конца ответа — их ключи page cache
    либо устаревают естественно, либо перезаписываются свежим контентом)."""
    from .models import InvalidationEpoch
    new = InvalidationEpoch.bump()
    _reset_epoch_cache()
    return new


