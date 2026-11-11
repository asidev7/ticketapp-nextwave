"""
Base settings shared by every environment.

Every secret and environment-specific value is read from `.env`
(see `.env.example`). Never commit the real `.env`.
"""
from datetime import timedelta
from pathlib import Path

import environ
from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me-in-production")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Public base URL, used in e-mails, payment callbacks and webhooks.
SITE_URL = env("SITE_URL", default="http://localhost:8000").rstrip("/")

# Admin lives at a non-default URL.
ADMIN_URL = env("ADMIN_URL", default="nwp-admin/").strip("/") + "/"

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "lineup",
    "tickets",
    "payments",
    "scanner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.CookieLocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database & cache
# --------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL", default="")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "nextwave",
        }
    }

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = ["core.auth_backends.EmailOrUsernameBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "scanner:login"
LOGIN_REDIRECT_URL = "/" + ADMIN_URL  # admin login without ?next=
SCANNER_GROUP_NAME = "Scanner"

# --------------------------------------------------------------------------
# Internationalisation — English by default, French second.
# Numbers, dates and amounts are always rendered in English format
# (see config/formats and core/templatetags/nwp.py).
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("fr", "Français"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
FORMAT_MODULE_PATH = ["config.formats"]
USE_I18N = True
USE_TZ = True
TIME_ZONE = "Africa/Porto-Novo"
USE_THOUSAND_SEPARATOR = False
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365

# --------------------------------------------------------------------------
# Static & media
# --------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Holder photos and generated tickets: never served directly,
# only through signed-token views.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", default=str(BASE_DIR / "private_media")))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
HOLDER_PHOTO_MAX_BYTES = 5 * 1024 * 1024

# --------------------------------------------------------------------------
# E-mail
# --------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=20)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="NextWave Party <tickets@nextwaveparty.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --------------------------------------------------------------------------
# Celery — falls back to synchronous execution when no Redis is configured.
# --------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL or "memory://")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=not bool(REDIS_URL))
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TASK_IGNORE_RESULT = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"max_retries": 1}
CELERY_BEAT_SCHEDULE = {
    "release-expired-orders": {
        "task": "tickets.tasks.release_expired_orders_task",
        "schedule": timedelta(minutes=1),
    },
}

# --------------------------------------------------------------------------
# NextWave specific
# --------------------------------------------------------------------------
# Fernet key used to encrypt payment gateway keys in the database.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")

# Secret used to sign QR codes (HMAC-SHA256). Changing it invalidates every issued ticket.
QR_SECRET_KEY = env("QR_SECRET_KEY", default=SECRET_KEY)

# Stock is reserved for this long while the buyer pays.
ORDER_RESERVATION_MINUTES = env.int("ORDER_RESERVATION_MINUTES", default=15)

# Gateway HTTP calls
PAYMENT_HTTP_TIMEOUT = env.int("PAYMENT_HTTP_TIMEOUT", default=20)
FEEXPAY_API_BASE = env("FEEXPAY_API_BASE", default="https://api.feexpay.me").rstrip("/")

# Ticket rendering
TICKET_PDF_ENGINE = env("TICKET_PDF_ENGINE", default="weasyprint")  # weasyprint | pillow
TICKET_FONT_REGULAR = env("TICKET_FONT_REGULAR", default="")
TICKET_FONT_BOLD = env("TICKET_FONT_BOLD", default="")

# Rate limits (django-ratelimit)
RATELIMIT_USE_CACHE = "default"
RATELIMIT_IP_META_KEY = env("RATELIMIT_IP_META_KEY", default=None)
CHECKOUT_RATE = env("CHECKOUT_RATE", default="20/h")
FIND_TICKET_RATE = env("FIND_TICKET_RATE", default="5/h")
SCANNER_LOGIN_RATE = env("SCANNER_LOGIN_RATE", default="10/15m")

# --------------------------------------------------------------------------
# Security defaults (hardened further in prod.py)
# --------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "payments": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "tickets": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "scanner": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "weasyprint": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "fontTools": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# --------------------------------------------------------------------------
# Admin — django-unfold
# --------------------------------------------------------------------------
UNFOLD = {
    "SITE_TITLE": "NextWave Party",
    "SITE_HEADER": "NextWave Party",
    "SITE_SUBHEADER": _("Ticketing admin"),
    "SITE_URL": "/",
    "SITE_SYMBOL": "confirmation_number",
    "SITE_FAVICONS": [
        {"rel": "icon", "sizes": "64x64", "type": "image/png", "href": lambda request: static("img/favicon.png")},
    ],
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_LANGUAGES": True,
    "DASHBOARD_CALLBACK": "core.dashboard.dashboard_callback",
    "STYLES": [lambda request: static("css/admin.css")],
    "COLORS": {
        "primary": {
            "50": "oklch(97% .014 254)",
            "100": "oklch(93.2% .032 255)",
            "200": "oklch(88.2% .059 254)",
            "300": "oklch(80.9% .105 251)",
            "400": "oklch(70.7% .165 254)",
            "500": "oklch(62.3% .214 259)",
            "600": "oklch(54.6% .245 262)",
            "700": "oklch(48.8% .243 264)",
            "800": "oklch(42.4% .199 265)",
            "900": "oklch(37.9% .146 265)",
            "950": "oklch(28.2% .091 267)",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": _("Sales"),
                "separator": False,
                "items": [
                    {"title": _("Dashboard"), "icon": "dashboard", "link": reverse_lazy("admin:index")},
                    {"title": _("Orders"), "icon": "shopping_cart", "link": reverse_lazy("admin:tickets_order_changelist")},
                    {"title": _("Tickets"), "icon": "confirmation_number", "link": reverse_lazy("admin:tickets_ticket_changelist")},
                    {"title": _("Complimentary tickets"), "icon": "redeem", "link": reverse_lazy("admin:tickets_complimentaryorder_changelist")},
                    {"title": _("Print batches"), "icon": "print", "link": reverse_lazy("admin:tickets_printbatch_changelist")},
                    {"title": _("Staff cards"), "icon": "badge", "link": reverse_lazy("admin:scanner_staffcard_changelist")},
                ],
            },
            {
                "title": _("Entrance"),
                "separator": True,
                "items": [
                    {"title": _("Scanner accesses"), "icon": "key", "link": reverse_lazy("admin:scanner_scanneraccess_changelist")},
                    {"title": _("Entries"), "icon": "how_to_reg", "link": reverse_lazy("admin:scanner_checkin_changelist")},
                    {"title": _("Scan logs"), "icon": "qr_code_scanner", "link": reverse_lazy("admin:scanner_scanlog_changelist")},
                ],
            },
            {
                "title": _("Event content"),
                "separator": True,
                "items": [
                    {"title": _("Event"), "icon": "event", "link": reverse_lazy("admin:core_event_changelist")},
                    {"title": _("YouTube videos"), "icon": "smart_display", "link": reverse_lazy("admin:core_eventvideo_changelist")},
                    {"title": _("Artists"), "icon": "mic_external_on", "link": reverse_lazy("admin:lineup_artist_changelist")},
                    {"title": _("Artist categories"), "icon": "category", "link": reverse_lazy("admin:lineup_artistcategory_changelist")},
                    {"title": _("Ticket types"), "icon": "sell", "link": reverse_lazy("admin:tickets_tickettype_changelist")},
                    {"title": _("Physical outlets"), "icon": "storefront", "link": reverse_lazy("admin:core_physicaloutlet_changelist")},
                    {"title": _("FAQ"), "icon": "help", "link": reverse_lazy("admin:core_faq_changelist")},
                ],
            },
            {
                "title": _("Configuration"),
                "separator": True,
                "items": [
                    {"title": _("Site settings"), "icon": "settings", "link": reverse_lazy("admin:core_sitesettings_changelist")},
                    {"title": _("Payment gateways"), "icon": "payments", "link": reverse_lazy("admin:payments_paymentgateway_changelist")},
                    {"title": _("Gateway logs"), "icon": "receipt_long", "link": reverse_lazy("admin:payments_paymentlog_changelist")},
                    {"title": _("Users"), "icon": "person", "link": reverse_lazy("admin:auth_user_changelist")},
                    {"title": _("Groups"), "icon": "group", "link": reverse_lazy("admin:auth_group_changelist")},
                ],
            },
        ],
    },
}
