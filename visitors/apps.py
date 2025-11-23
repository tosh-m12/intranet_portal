from django.apps import AppConfig


class VisitorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "visitors"

    def ready(self):
        """
        Django 起動時にバックグラウンドスケジューラを起動。
        実際に送信するかどうかは VisitMailConfig.mode で制御。
        二重起動防止は scheduler 側の _scheduler_started で行う。
        """
        from .scheduler import start_scheduler

        print("[DEBUG] VisitorsConfig.ready() called")  # ← 一旦確認用
        start_scheduler()