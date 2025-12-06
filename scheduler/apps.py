# scheduler/apps.py
from django.apps import AppConfig
import threading
import time as time_module
from django.utils import timezone
from django.core.management import call_command
from datetime import timedelta


class SchedulerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scheduler'

    def ready(self):
        start_scheduler_loop()


def scheduler_loop():
    print("### envmon scheduler loop start")

    last_run_date_envmon = None
    first_loop = True

    while True:
        # ★ ここでローカルタイムに変換する（重要）
        now = timezone.localtime(timezone.now())

        try:
            # EnvSettings は毎回取り直し（設定変更を反映させる）
            from envmon.models import EnvSettings

            settings_obj = EnvSettings.get_solo()
            target_time = settings_obj.history_fetch_time  # time 型（ローカル前提）

            if first_loop:
                # 起動直後は「今日分を済みにするか／していないか」だけ決めて、
                # このループでは実行しない
                if now.time() >= target_time:
                    # すでに今日の target_time は過ぎている
                    # → 今日分は「済み」とみなす（次の実行は明日）
                    last_run_date_envmon = now.date()
                else:
                    # まだ target_time 前
                    # → 今日の target_time で実行させたいので、
                    #    last_run_date_envmon を昨日にしておく
                    last_run_date_envmon = now.date() - timedelta(days=1)

                print(
                    f"[ENV_SCHED] init: now={now}, "
                    f"target_time={target_time}, "
                    f"last_run_date_envmon={last_run_date_envmon}"
                )
                first_loop = False

            else:
                # 通常の1日1回判定（ローカル日付・ローカル時刻で判定）
                print(
                    f"[ENV_SCHED] tick now={now}, "
                    f"target_time={target_time}, "
                    f"last_run_date_envmon={last_run_date_envmon}"
                )

                if last_run_date_envmon != now.date() and now.time() >= target_time:
                    print(f"[ENV_SCHED] running envmon fetch_env_history at {now}")
                    try:
                        call_command("fetch_env_history")
                    except Exception as e:
                        print(f"[ENV_SCHED] error while calling fetch_env_history: {e}")
                    else:
                        last_run_date_envmon = now.date()

        except Exception as e:
            print(f"[SCHEDULER] error in scheduler_loop: {e}")

        time_module.sleep(60)


def start_scheduler_loop():
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
