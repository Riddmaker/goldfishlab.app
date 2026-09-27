"""Goldfish Lab - it actually plays your deck."""

from .celery import app as celery_app

__all__ = ("celery_app",)
