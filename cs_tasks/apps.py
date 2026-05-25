# cs_tasks/apps.py
from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class CsTasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cs_tasks"

    def ready(self):
        """アプリ起動時に一度だけ週報スケジューラを起動する。"""
        try:
            from .scheduler import start_scheduler
            start_scheduler()
        except Exception:
            logger.exception("[CSTASKS_SCHED] failed to start scheduler")
