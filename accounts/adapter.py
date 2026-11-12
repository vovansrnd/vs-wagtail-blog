"""Fail-soft adapter for allauth email transport errors.

A caught SMTP/network exception means delivery was NOT confirmed. The adapter
only prevents a transport failure from turning the signup/login HTTP request
into a 500; allauth may still render its generic "check your email" response.
It does not mark an EmailAddress verified and does not grant an authenticated
session. The rejection is logged to the application error log.

This module does not change EMAIL_BACKEND, SMTP host, credentials, or sender.
"""
import logging
import socket
import ssl
from smtplib import SMTPException

from allauth.account.adapter import DefaultAccountAdapter

# Логи идут в django_errors.log (см. LOGGING в base.py). A successful HTTP
# response from this adapter is not evidence that the email was delivered.
logger = logging.getLogger("accounts.mail")

# Д57-Б (замечание координатора): SMTPException — подкласс OSError, поэтому
# «except (SMTPException, OSError)» фактически ловил ЛЮБОЙ OSError, включая
# FileNotFoundError/PermissionError — локальные ошибки приложения. Перехват
# сужен до реальных отказов SMTP-пути: SMTP-исключения + конкретные сетевые
# ошибки (соединение/таймаут/DNS/TLS). Прочий OSError пробрасывается наружу.
SMTP_DELIVERY_ERRORS = (
    SMTPException,        # SMTPDataError (554), SMTPServerDisconnected, ...
    ConnectionError,      # ConnectionRefusedError, ConnectionResetError, ...
    TimeoutError,         # socket.timeout (Python 3.10+)
    socket.gaierror,      # DNS-разрешение smtp-хоста
    ssl.SSLError,         # TLS-рукопожатие/обрыв ("EOF detected" и т.п.)
)


class ResilientAccountAdapter(DefaultAccountAdapter):
    """DefaultAccountAdapter: отказ SMTP не роняет HTTP-запрос."""

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        try:
            super().send_mail(template_prefix, email, context)
        except SMTP_DELIVERY_ERRORS as exc:
            logger.error(
                "Доставка письма %s на %s не удалась: %s: %s",
                template_prefix,
                email,
                type(exc).__name__,
                exc,
            )
