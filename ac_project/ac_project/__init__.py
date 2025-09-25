# Celery instance is defined in ac_project/celery.py
# It creates celery_app object and points it to Django settings
# celery_app becomes the singleton task queue app for your whole project
from .celery import celery_app

# 'from ac_project import *', only exports celery_app
__all__ = ("celery_app",)

""" When you run Celery workers, "celery -A ac_project worker -l info"
    The -A ac_project means:
    Import ac_project/__init__.py →
    which exposes celery_app →  now Celery knows what to run. """
