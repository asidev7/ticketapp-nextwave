#!/bin/sh
# Container entrypoint: wait for the database, migrate (web only), then run the command.
set -e

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput
fi

exec "$@"
