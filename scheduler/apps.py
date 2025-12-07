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
    last_run_cache = None  # キャッシュ用

    while True:
        now = timezone.localtime(timezone.now())

        try:
            from envmon.models import EnvSettings

            settings_obj = EnvSettings.get_solo()
            target_time = settings_obj.history_fetch_time  # 履歴取り込み時刻 (time 型)
            cache_interval = settings_obj.cache_interval or 300

            # ===== 1. 履歴取り込み（1日1回） =====
            if first_loop:
                # ★ 初回は「すでにその日の target_time を過ぎているなら実行済み扱い」にする
                if now.time() >= target_time:
                    last_run_date_envmon = now.date()
                else:
                    last_run_date_envmon = now.date() - timedelta(days=1)

                print(
                    f"[ENV_SCHED] init: now={now}, "
                    f"target_time={target_time}, "
                    f"last_run_date_envmon={last_run_date_envmon}"
                )
                first_loop = False

            else:
                print(
                    f"[ENV_SCHED] tick now={now}, "
                    f"target_time={target_time}, "
                    f"last_run_date_envmon={last_run_date_envmon}"
                )

                # ★ 日付が変わっていて、かつ target_time を過ぎていたら 1 回だけ実行
                if last_run_date_envmon != now.date() and now.time() >= target_time:
                    print(f"[ENV_SCHED] running envmon fetch_env_history at {now}")
                    try:
                        call_command("fetch_env_history")
                    except Exception as e:
                        print(f"[ENV_SCHED] error while calling fetch_env_history: {e}")
                    else:
                        last_run_date_envmon = now.date()

            # ===== 2. 5分おきキャッシュ（ただし履歴取得中は止める） =====
            if settings_obj.is_fetching_history:
                # 履歴取得中は run_env_cache 実行しない（token 競合防止）
                print(
                    f"[ENV_SCHED] is_fetching_history=True, "
                    f"skip run_env_cache at {now}"
                )
            else:
                if last_run_cache is None:
                    # 起動直後はすぐ1回実行
                    print(f"[ENV_SCHED] running run_env_cache (first time) at {now}")
                    try:
                        call_command("run_env_cache")
                    except Exception as e:
                        print(f"[ENV_SCHED] error while calling run_env_cache: {e}")
                    else:
                        last_run_cache = now
                else:
                    elapsed = (now - last_run_cache).total_seconds()
                    print(
                        f"[ENV_SCHED] cache tick now={now}, "
                        f"elapsed={elapsed:.0f}sec, interval={cache_interval}sec"
                    )
                    if elapsed >= cache_interval:
                        print(f"[ENV_SCHED] running run_env_cache at {now}")
                        try:
                            call_command("run_env_cache")
                        except Exception as e:
                            print(f"[ENV_SCHED] error while calling run_env_cache: {e}")
                        else:
                            last_run_cache = now

        except Exception as e:
            print(f"[SCHEDULER] error in scheduler_loop: {e}")

        time_module.sleep(60)



def start_scheduler_loop():
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
