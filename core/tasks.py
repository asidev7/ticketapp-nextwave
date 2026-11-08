"""Helpers to run Celery tasks with a synchronous fallback."""
import logging

from django.conf import settings

logger = logging.getLogger("tickets")


def dispatch(task, *args, **kwargs):
    """Queue `task` on Celery, or run it in-process when Redis is absent.

    - CELERY_TASK_ALWAYS_EAGER=True (default without REDIS_URL): run now.
    - Broker configured but unreachable: log and run now instead of failing
      the request (a paid customer must always get their tickets).
    """
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return _run_sync(task, *args, **kwargs)
    try:
        return task.delay(*args, **kwargs)
    except Exception:  # broker down, connection refused…
        logger.warning("Celery broker unavailable, running %s synchronously", task.name)
        return _run_sync(task, *args, **kwargs)


def _run_sync(task, *args, **kwargs):
    try:
        return task.run(*args, **kwargs)
    except Exception:
        logger.exception("Task %s failed", task.name)
        return None
