"""Д57/Д57-Б: отказ SMTP не роняет HTTP-запрос и НЕ обходит верификацию.

Условия координатора (Д57-Б): при отказе доставки письма
1) пользователь НЕ получает аутентифицированную сессию без подтверждения
   email; 2) адрес НЕ помечается подтверждённым автоматически;
3) ошибка не превращается в 500; 4) исключение ограничено SMTP-отказом —
ошибки приложения НЕ подавляются. Конфигурация почты не меняется.
"""
import smtplib
from unittest import mock

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase

from .tests_signup_protection import SIGNUP_URL, _get_signup_page

User = get_user_model()

SMTP_554 = smtplib.SMTPDataError(554, b"5.7.1 Message rejected under suspicion of SPAM")


class ResilientMailTests(TestCase):
    """SMTP-отказ при отправке письма подтверждения: без 500 и без обхода
    обязательной верификации (ACCOUNT_EMAIL_VERIFICATION = mandatory)."""

    def setUp(self):
        caches["default"].clear()
        caches["security"].clear()

    @staticmethod
    def _patch_send(exc=SMTP_554):
        return mock.patch(
            "django.core.mail.message.EmailMessage.send",
            side_effect=exc,
        )

    def test_signup_survives_smtp_554(self):
        """Регистрация при отказе SMTP: пользователь создан, 302, факт в логе,
        сессия НЕ аутентифицирована, адрес НЕ помечен подтверждённым."""
        with self.assertLogs("accounts.mail", level="ERROR"), self._patch_send():
            _r, task_id, _q, answer = _get_signup_page(self.client)
            resp = self.client.post(
                SIGNUP_URL,
                {
                    "email": "mx@example.test",
                    "username": "mxuser",
                    "password1": "Strong-Pass-9x7",
                    "password2": "Strong-Pass-9x7",
                    "company": "",
                    "math_token": task_id,
                    "math_answer": str(answer),
                    "parent": "",
                },
            )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.filter(username="mxuser").first()
        self.assertIsNotNone(user, "пользователь должен быть создан")
        # mandatory-верификация сохранена: без подтверждения email сессии нет
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(
            EmailAddress.objects.get(user=user).verified,
            "SMTP-отказ не должен автоматически подтверждать адрес",
        )

    def test_unverified_login_survives_smtp_554(self):
        """Вход неподтверждённого при отказе SMTP: не 500, ведёт на
        подтверждение почты, аутентифицированной сессии нет."""
        user = User.objects.create_user(
            username="old2", password="pw-1234567", email="old2@example.test"
        )
        EmailAddress.objects.create(
            user=user, email=user.email, verified=False, primary=True
        )
        with self._patch_send():
            resp = self.client.post(
                "/accounts/login/", {"login": "old2", "password": "pw-1234567"}
            )
        self.assertNotEqual(resp.status_code, 500)
        # allauth отводит на страницу "подтвердите email" — сессия чистая
        self.assertIn("confirm-email", resp["Location"])
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(EmailAddress.objects.get(user=user).verified)

    def test_network_errors_still_handled(self):
        """Сетевые отказы SMTP-пути (соединение/таймаут/обрыв) обрабатываются,
        запрос не роняется — перехват не сломан сужением исключений (Д57-Б)."""
        user = User.objects.create_user(
            username="old4", password="pw-1234567", email="old4@example.test"
        )
        EmailAddress.objects.create(
            user=user, email=user.email, verified=False, primary=True
        )
        for exc in (
            ConnectionRefusedError("connection refused"),
            TimeoutError("timed out"),
            smtplib.SMTPServerDisconnected("disconnected"),
        ):
            with self.subTest(exc=type(exc).__name__), self._patch_send(exc):
                resp = self.client.post(
                    "/accounts/login/", {"login": "old4", "password": "pw-1234567"}
                )
            self.assertNotEqual(resp.status_code, 500)

    def test_foreign_oserror_propagates(self):
        """Д57-Б п.4: посторонний OSError (FileNotFoundError — локальная
        ошибка приложения) НЕ подавляется, пробрасывается наружу.
        SMTPException — подкласс OSError, поэтому перехват сужен до
        SMTP_DELIVERY_ERRORS и FileNotFoundError в него не попадает."""
        user = User.objects.create_user(
            username="old3", password="pw-1234567", email="old3@example.test"
        )
        EmailAddress.objects.create(
            user=user, email=user.email, verified=False, primary=True
        )
        with self._patch_send(FileNotFoundError("local app bug")):
            with self.assertRaises(FileNotFoundError):
                self.client.post(
                    "/accounts/login/", {"login": "old3", "password": "pw-1234567"}
                )
