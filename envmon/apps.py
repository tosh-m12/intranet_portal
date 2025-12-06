from django.apps import AppConfig


class EnvmonConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'envmon'

    def ready(self):
        """
        サーバー起動時に、もし is_fetching_history が True のままなら
        強制的に False に戻しておく（異常終了対策）。
        """
        from django.db.utils import OperationalError, ProgrammingError
        from .models import EnvSettings

        try:
            env = EnvSettings.get_solo()
            if env.is_fetching_history:
                env.is_fetching_history = False
                env.save(update_fields=["is_fetching_history"])
                print("[ENV_SCHED] reset is_fetching_history=True -> False on startup")
        except (OperationalError, ProgrammingError):
            # migrate 前など、テーブルがまだ無い場合は無視
            pass