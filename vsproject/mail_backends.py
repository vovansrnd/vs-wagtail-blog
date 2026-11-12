"""Optional sendmail backend for hosts that provide a local sendmail-compatible MTA.

Enable it only after confirming the binary path and delivery policy with the
hosting provider. This is a custom Django backend, not a Wagtail feature.
"""

import logging
import subprocess
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.validators import validate_email
from django.utils.encoding import force_str

logger = logging.getLogger(__name__)
SENDMAIL_BINARY = getattr(settings, "SENDMAIL_BINARY", "/usr/sbin/sendmail")
SENDMAIL_TIMEOUT = int(getattr(settings, "SENDMAIL_TIMEOUT", 15))


class SendmailBackend(BaseEmailBackend):
    """Deliver Django messages through a local sendmail-compatible binary.

    Recipients are passed as envelope arguments rather than relying on `-t`.
    This is important because BCC recipients are intentionally absent from the
    serialized message headers but are still returned by EmailMessage.recipients().
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        for message in email_messages:
            if self._send(message):
                sent += 1
        return sent

    @staticmethod
    def _mailbox(value):
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise ValueError("Invalid email address")
        _display_name, address = parseaddr(value)
        if not address or address.startswith("-"):
            raise ValueError("Invalid email address")
        validate_email(address)
        return address

    def _failure(self, error):
        logger.error("Sendmail delivery failed: %s", error)
        if self.fail_silently:
            return False
        if isinstance(error, BaseException):
            raise RuntimeError(f"Sendmail delivery failed: {error}") from error
        raise RuntimeError(f"Sendmail delivery failed: {error}")

    def _send(self, message):
        try:
            recipients_method = getattr(message, "recipients", None)
            raw_recipients = (
                recipients_method()
                if callable(recipients_method)
                else (
                    list(message.to or [])
                    + list(message.cc or [])
                    + list(message.bcc or [])
                )
            )
            recipients = list(
                dict.fromkeys(self._mailbox(item) for item in raw_recipients)
            )
            if not recipients:
                return False

            sender_value = getattr(message, "from_email", None) or getattr(
                settings, "DEFAULT_FROM_EMAIL", ""
            )
            sender = self._mailbox(sender_value) if sender_value else None

            mime_message = message.message()
            # BCC must never leak into message headers. Its recipients are passed
            # separately on the command line below.
            for header in ("Bcc", "Resent-Bcc"):
                if header in mime_message:
                    del mime_message[header]
            raw = mime_message.as_bytes()

            command = [SENDMAIL_BINARY, "-oi"]
            if sender:
                command.extend(["-f", sender])
            command.extend(recipients)

            proc = subprocess.run(
                command,
                input=raw,
                capture_output=True,
                timeout=SENDMAIL_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._failure(f"sendmail timed out after {SENDMAIL_TIMEOUT}s")
        except (OSError, ValueError, TypeError) as exc:
            return self._failure(exc)

        if proc.returncode != 0:
            stderr = force_str(proc.stderr or "").strip()[:500]
            return self._failure(
                f"exit={proc.returncode}; stderr={stderr or '(empty)'}"
            )
        return True
