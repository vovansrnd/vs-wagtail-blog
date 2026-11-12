"""Защита административных точек входа от перебора паролей.

Обычная авторизация через /accounts/login/ не должна сбрасывать счётчик
ошибок административного входа с того же IP. Счётчик очищается только после
успешной авторизации staff-пользователя именно через admin login URL.
"""
import time
import logging

from django.contrib.auth.signals import user_login_failed, user_logged_in
from django.core.cache import caches
from django.http import JsonResponse
from django.dispatch import receiver

security = caches["security"]
seclog = logging.getLogger("blog.security")

FAIL_LIMIT = 10
WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60
LOGIN_PATH_PREFIXES = ("/admin/login", "/django-admin/login")


def _is_admin_login_path(path: str) -> bool:
    """Match the login endpoint and its slash/nested variants, not lookalikes."""
    path = (path or "").rstrip("/") or "/"
    return any(path == prefix or path.startswith(prefix + "/") for prefix in LOGIN_PATH_PREFIXES)


def _key(ip: str) -> str:
    return f"login_fail:ip:{ip}"


def is_locked(ip: str) -> bool:
    return len(security.get(_key(ip)) or []) >= FAIL_LIMIT


def _client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR", "")


class LoginRateLimitMiddleware:
    """Отклоняет POST на административный login URL при активной блокировке."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and _is_admin_login_path(request.path):
            ip = _client_ip(request)
            if is_locked(ip):
                seclog.warning("Login blocked (rate limit): ip=%s path=%s", ip, request.path)
                response = JsonResponse(
                    {"error": "Слишком много неудачных попыток входа. Повторите позже."},
                    status=429,
                )
                response["Retry-After"] = str(LOCK_SECONDS)
                return response
        return self.get_response(request)


@receiver(user_login_failed)
def record_failed_login(sender, credentials, request, **kwargs):
    # Один неудачный POST может вызвать authenticate() дважды; считаем POST один раз.
    if request is None or getattr(request, "_login_fail_recorded", False):
        return
    if not _is_admin_login_path(request.path):
        return
    request._login_fail_recorded = True
    ip = _client_ip(request)
    now = time.time()
    window_start = now - WINDOW_SECONDS
    key = _key(ip)
    entries = [timestamp for timestamp in (security.get(key) or []) if timestamp > window_start]
    entries.append(now)
    security.set(key, entries, LOCK_SECONDS)


@receiver(user_logged_in)
def clear_failed_logins(sender, request, user, **kwargs):
    """Only a successful staff login via the admin endpoint clears this counter."""
    if request is None or not _is_admin_login_path(request.path):
        return
    if not getattr(user, "is_staff", False):
        return
    security.delete(_key(_client_ip(request)))
