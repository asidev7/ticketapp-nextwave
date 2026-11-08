from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import SECRET_KEY, env

DEBUG = False

if SECRET_KEY.startswith("dev-insecure") or len(SECRET_KEY) < 40:
    raise ImproperlyConfigured("Set a strong SECRET_KEY in .env for production.")

FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")  # required in production
QR_SECRET_KEY = env("QR_SECRET_KEY")  # required in production

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# HTTPS everywhere (Nginx terminates TLS and forwards X-Forwarded-Proto).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
LANGUAGE_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)

# Behind Nginx the real client IP is in X-Real-IP (see deploy/nginx.conf).
RATELIMIT_IP_META_KEY = env("RATELIMIT_IP_META_KEY", default="HTTP_X_REAL_IP")
