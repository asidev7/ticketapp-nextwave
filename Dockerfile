FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    WEB_CONCURRENCY=3

# WeasyPrint needs Pango; Liberation Sans is metric-compatible with Arial;
# gettext compiles translations.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
        fonts-liberation gettext \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Build-time only values (prod settings refuse to load without them)
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/media /app/private_media /app/staticfiles \
    && chmod +x /app/deploy/entrypoint.sh \
    && export SECRET_KEY=build-only-key-not-used-at-runtime-0123456789abcdef \
              FIELD_ENCRYPTION_KEY=MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY= \
              QR_SECRET_KEY=build-only \
    && python manage.py collectstatic --noinput \
    && python manage.py compilemessages --ignore=.venv \
    && chown -R app:app /app

USER app
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
# Gunicorn reads the number of workers from WEB_CONCURRENCY
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "60", "--access-logfile", "-"]
