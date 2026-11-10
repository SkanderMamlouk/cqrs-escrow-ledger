"""
Django settings for the CQRS & Event Sourcing banking ledger.

Every value that can change between computers (database mode, AI provider,
API keys...) is read from environment variables, which are usually written
in the `.env` file at the project root.  Copy `.env.example` to `.env` and
edit it - you never need to touch this Python file.
"""

from __future__ import annotations

import os
from pathlib import Path

from config import dbmode

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader: KEY=VALUE lines; real environment variables win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me-ds1-project14")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "readmodel",
    "ai_audit",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "web.context_processors.system_status",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# The two databases of CQRS (see config/dbmode.py for the full explanation)
# --------------------------------------------------------------------------
DB_MODE, EVENTSTORE_ENV, _READ_DB = dbmode.resolve(BASE_DIR)
DATABASES = {"default": _READ_DB}  # Django only ever talks to the READ database
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Event sourcing options
SNAPSHOT_INTERVAL = int(os.environ.get("SNAPSHOT_INTERVAL", "5"))  # snapshot every N events
EVENTSTORE_ENV["SNAPSHOT_INTERVAL"] = str(SNAPSHOT_INTERVAL)
PROJECTOR_POLL_SECONDS = float(os.environ.get("PROJECTOR_POLL_SECONDS", "0.2"))
# After a command, the web page waits up to this long for the projector
# ("read your own writes") and displays the measured eventual-consistency lag.
READ_WAIT_SECONDS = float(os.environ.get("READ_WAIT_SECONDS", "2.5"))

# Business rules for webhook alerts (amounts in TND)
ALERT_LARGE_TRANSFER_TND = os.environ.get("ALERT_LARGE_TRANSFER_TND", "10000")
ALERT_LOW_BALANCE_TND = os.environ.get("ALERT_LOW_BALANCE_TND", "100")
WEBHOOK_URLS = [u.strip() for u in os.environ.get("WEBHOOK_URLS", "http://127.0.0.1:8000/webhooks/receive/").split(",") if u.strip()]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me-webhook-secret")

# AI audit assistant
AI_PROVIDER = os.environ.get("AI_PROVIDER", "auto")  # auto | ollama | gemini | deterministic
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
AI_TIMEOUT_SECONDS = float(os.environ.get("AI_TIMEOUT_SECONDS", "120"))
AI_MAX_ATTEMPTS = int(os.environ.get("AI_MAX_ATTEMPTS", "2"))

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Tunis"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "var" / "staticfiles"
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s [%(name)s] %(levelname)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {
        "ledger": {"handlers": ["console"], "level": "INFO"},
        "pgserver": {"level": "WARNING"},
    },
}
