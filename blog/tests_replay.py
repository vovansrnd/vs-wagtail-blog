"""Тесты DB-backed replay protection (Этап 2 ТЗ, переработка)."""
import hashlib
import secrets
import time

from django.core.cache import caches
from django.test import TestCase

from blog.models import UsedFormToken


class ReplayProtectionTests(TestCase):
    def setUp(self):
        caches["security"].clear()
        self.nonce = secrets.token_urlsafe(18)
        self.token_hash = hashlib.sha256(self.nonce.encode()).hexdigest()

    def _register(self, nonce):
        from django.db import IntegrityError
        h = hashlib.sha256(nonce.encode()).hexdigest()
        try:
            UsedFormToken.objects.create(token_hash=h)
            return True
        except IntegrityError:
            return False

    def test_first_use_accepted(self):
        self.assertTrue(self._register(self.nonce))

    def test_replay_rejected(self):
        self.assertTrue(self._register(self.nonce))
        self.assertFalse(self._register(self.nonce), "повтор должен быть отклонён")

    def test_survives_security_cache_eviction(self):
        """Вытеснение security-кэша не разрешает повтор."""
        self._register(self.nonce)
        caches["security"].clear()
        self.assertFalse(self._register(self.nonce),
                         "Повтор после cache.clear() должен быть отклонён")

    def test_survives_all_cache_eviction(self):
        """Вытеснение ВСЕХ кэшей не разрешает повтор."""
        self._register(self.nonce)
        for alias in ("default", "security", "pages"):
            caches[alias].clear()
        self.assertFalse(self._register(self.nonce))

    def test_different_tokens_accepted(self):
        self.assertTrue(self._register("nonce-a"))
        self.assertTrue(self._register("nonce-b"))

    def test_cleanup_removes_expired(self):
        from datetime import timedelta
        from django.utils import timezone
        from django.conf import settings
        from blog.models import UsedFormToken as M
        max_age = int(getattr(settings, "CONTACT_FORM_TOKEN_MAX_AGE", 24 * 60 * 60))
        old = M.objects.create(token_hash="old-hash")
        M.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(seconds=max_age + 1))
        fresh = M.objects.create(token_hash="fresh-hash")
        cutoff = timezone.now() - timedelta(seconds=max_age)
        M.objects.filter(created_at__lt=cutoff).delete()
        self.assertFalse(M.objects.filter(pk=old.pk).exists())
        self.assertTrue(M.objects.filter(pk=fresh.pk).exists())
