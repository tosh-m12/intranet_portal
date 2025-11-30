# visitors/apps.py
from django.apps import AppConfig
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class VisitorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "visitors"

    def ready(self):
        """
        アプリ起動時に一度だけスケジューラを起動する。
        """
        # 管理コマンドなどでも ready() が呼ばれるので、
        # 「本番サーバ起動時だけにしたい」などあれば条件を入れてもよい
        try:
            from .scheduler import start_scheduler
            start_scheduler()
        except Exception:
            logger.exception("[VISITOR_SCHED] failed to start scheduler")
