import sys
import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0", "[::1]"])

# Print e-mails in the console unless an SMTP server is configured.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")

# `manage.py test`: keep generated files out of the real media folders.
if "test" in sys.argv:
    PRIVATE_MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="nwp-test-private-"))
    MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="nwp-test-media-"))
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests
