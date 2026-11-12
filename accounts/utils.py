from django.templatetags.static import static


def get_user_avatar_url(user):
    if not user or not getattr(user, "is_authenticated", False):
        return static("img/guest_avatar.png")

    try:
        return user.profile.get_avatar_url()
    except Exception:
        return static("img/default_avatar.png")
