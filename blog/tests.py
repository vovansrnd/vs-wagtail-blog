from pathlib import Path
from unittest.mock import Mock, patch

from django.core.mail import EmailMessage
from django.test import SimpleTestCase

from vsproject.mail_backends import SendmailBackend


class SendmailBackendTests(SimpleTestCase):
    @patch("vsproject.mail_backends.subprocess.run")
    def test_to_cc_and_bcc_are_envelope_recipients(self, run):
        run.return_value = Mock(returncode=0, stderr=b"")
        message = EmailMessage(
            subject="Test",
            body="Body",
            from_email="sender@example.test",
            to=["to@example.test"],
            cc=["cc@example.test"],
            bcc=["bcc@example.test"],
        )

        sent = SendmailBackend().send_messages([message])

        self.assertEqual(sent, 1)
        command = run.call_args.args[0]
        self.assertIn("to@example.test", command)
        self.assertIn("cc@example.test", command)
        self.assertIn("bcc@example.test", command)
        self.assertNotIn("-t", command)
        self.assertNotIn(b"Bcc:", run.call_args.kwargs["input"])

    @patch("vsproject.mail_backends.subprocess.run")
    def test_nonzero_exit_raises_unless_fail_silently(self, run):
        run.return_value = Mock(returncode=75, stderr=b"temporary failure")
        message = EmailMessage(
            subject="Test", body="Body", from_email="sender@example.test",
            to=["to@example.test"],
        )
        with self.assertRaises(RuntimeError):
            SendmailBackend(fail_silently=False).send_messages([message])
        self.assertEqual(SendmailBackend(fail_silently=True).send_messages([message]), 0)

    @patch("vsproject.mail_backends.subprocess.run")
    def test_timeout_is_suppressed_only_when_requested(self, run):
        from subprocess import TimeoutExpired

        run.side_effect = TimeoutExpired("sendmail", 15)
        message = EmailMessage(
            subject="Test", body="Body", from_email="sender@example.test",
            to=["to@example.test"],
        )
        self.assertEqual(SendmailBackend(fail_silently=True).send_messages([message]), 0)
        with self.assertRaises(RuntimeError):
            SendmailBackend(fail_silently=False).send_messages([message])


class PublicCommentTemplateTokenTests(SimpleTestCase):
    """Форма комментария грузится лениво приватным фрагментом:
    компонент темы — ленивый слот, токены живут во фрагменте."""

    def test_theme_components_use_lazy_slot(self):
        for theme in ("nova", "zenway"):
            path = Path(__file__).parent / f"templates/blog/{theme}/components/comments.html"
            with self.subTest(theme=theme):
                source = path.read_text(encoding="utf-8")
                self.assertIn("data-comment-form-lazy", source)
                self.assertIn("comment-form-lazy.js", source)
                self.assertNotIn("{{ form.form_token }}", source, "форма не должна быть inline")
        floppy = Path(__file__).parent / "templates/blog/floppy/blog_page.html"
        self.assertIn("data-comment-form-lazy", floppy.read_text(encoding="utf-8"))

    def test_fragment_partials_keep_signed_form_token(self):
        for theme in ("nova", "zenway", "floppy"):
            path = Path(__file__).parent / f"templates/blog/{theme}/components/comment_form_fragment.html"
            with self.subTest(theme=theme):
                source = path.read_text(encoding="utf-8")
                self.assertIn("{{ form.form_token }}", source)
                self.assertIn("csrf_token", source)
                self.assertNotIn("{{ form.submit_time }}", source)
        # список ответов остаётся в компоненте (рекурсивный рендер)
        nova = Path(__file__).parent / "templates/blog/nova/components/comments.html"
        self.assertIn("comment.children.all", nova.read_text(encoding="utf-8"))
        self.assertNotIn("comment.get_children", nova.read_text(encoding="utf-8"))
