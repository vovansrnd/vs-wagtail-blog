"""Optional social-avatar fetching on login.

This receiver performs outbound HTTP requests and is deliberately not loaded by
default. Enable AVATAR_AUTO_FETCH_ON_LOGIN only after reviewing provider access,
request timeouts, response-size limits and the hosting provider's network policy.
"""
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .models import UserProfile
from .services import fetch_avatar_for_user


@receiver(user_logged_in)
def fetch_avatar_on_first_login(sender, request, user, **kwargs):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if profile.avatar or profile.avatar_checked_at:
        return
    fetch_avatar_for_user(user)
