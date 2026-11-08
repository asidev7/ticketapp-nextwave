from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Log in with e-mail address or username (scanner app and admin)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if not username or not password:
            return None
        User = get_user_model()
        lookup = {"email__iexact": username} if "@" in username else {"username": username}
        users = list(User._default_manager.filter(**lookup)[:2])
        if len(users) != 1:
            # Unknown or ambiguous e-mail: run the hasher anyway (timing safety).
            User().set_password(password)
            return None
        user = users[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
