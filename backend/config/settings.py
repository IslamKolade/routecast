"""
Django settings for the RouteCast API.

The API is stateless: it calls third-party services (TomTom, Open-Meteo),
computes risk and returns JSON. There are no models, so no database is
configured. All secrets and deployment-specific values come from environment
variables (a local `.env` file is loaded in development).
"""
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-secret-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# Render exposes the public hostname of the service in this variable.
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

INSTALLED_APPS = [
    "corsheaders",
    "rest_framework",
    "planner",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Stateless API: no database.
DATABASES: dict = {}

USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
USE_I18N = False

CACHES = {
    # Weather and geocoding lookups: fast, per worker process.
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "routecast",
        "OPTIONS": {"MAX_ENTRIES": 20000},
    },
    # Small state every gunicorn worker must see: plan drafts for the weather relay
    # and the "server is rate-limited" flag. File-based so it is shared on the instance.
    "shared": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.environ.get("CACHE_DIR", os.path.join(tempfile.gettempdir(), "routecast-cache")),
        "OPTIONS": {"MAX_ENTRIES": 2000},
    },
}

# Relayed weather for a long trip can be a couple of MB of JSON.
DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "planner.exceptions.api_exception_handler",
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
CORS_ALLOWED_ORIGIN_REGEXES = env_list("CORS_ALLOWED_ORIGIN_REGEXES")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

# --- Application settings -------------------------------------------------

TOMTOM_API_KEY = os.environ.get("TOMTOM_API_KEY", "")
# Geocoding is restricted to North America because the rules use US units (mph, lb, in/hr).
GEOCODE_COUNTRY_SET = os.environ.get("GEOCODE_COUNTRY_SET", "US,CA,MX")
# Typical empty weight of a tractor + 53 ft trailer. TomTom's truck routing expects the
# *gross* vehicle weight, so we add this to the cargo weight the user enters.
EMPTY_TRUCK_WEIGHT_LB = int(os.environ.get("EMPTY_TRUCK_WEIGHT_LB", "35000"))
HTTP_TIMEOUT_SECONDS = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "20"))
WEATHER_CACHE_SECONDS = int(os.environ.get("WEATHER_CACHE_SECONDS", "1800"))
