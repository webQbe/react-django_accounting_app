from __future__ import annotations
import os
from celery import Celery

# ensure Django settings are set for Celery
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ac_project.settings")

# name should match your project package
celery_app = Celery("ac_project")

# read config from Django settings, using CELERY_ prefix
celery_app.config_from_object("django.conf:settings", namespace="CELERY")

# autoload tasks from installed apps
celery_app.autodiscover_tasks()

