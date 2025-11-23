import threading
import time
from datetime import time as dtime

from django.utils import timezone
import logging

from .models import VisitMailConfig
from .email_utils import send_daily_email

logger = logging.getLogger(__name__)

_scheduler_started = False


def scheduler_loop():
    """
    1分ごとに現在時刻と設定時刻を比較し、
    VisitMailConfig.mode が 'django' の場合に1日1回だけ send_daily_email() を実行する。
    """
    logger.info("[VISITOR_MAIL_SCHED] scheduler_loop started")
    print("[DEBUG] scheduler_loop started")  # ← 追加
    last_run_date = None

    while True:
        try:
            now = timezone.localtime()
            config, _ = VisitMailConfig.objects.get_or_create(pk=1)
            send_time = config.send_time or dtime(9, 0)
            mode = config.mode or VisitMailConfig.MODE_WINDOWS

            # デバッグ表示
            print(f"[DEBUG] scheduler tick now={now}, mode={mode}, send_time={send_time}")

            if mode != VisitMailConfig.MODE_DJANGO:
                last_run_date = None
            else:
                target = now.replace(
                    hour=send_time.hour,
                    minute=send_time.minute,
                    second=0,
                    microsecond=0,
                )

                if now >= target and (last_run_date is None or last_run_date != now.date()):
                    logger.info(
                        f"[VISITOR_MAIL_SCHED] time reached: now={now}, send_time={send_time}, "
                        "calling send_daily_email()"
                    )
                    print("[DEBUG] calling send_daily_email() from scheduler_loop")
                    result = send_daily_email()
                    last_run_date = now.date()
                    logger.info(f"[VISITOR_MAIL_SCHED] result={result}")
                    print(f"[DEBUG] send_daily_email() result={result}")

        except Exception as e:
            logger.error(f"[VISITOR_MAIL_SCHED] error in scheduler_loop: {e}", exc_info=True)
            print(f"[DEBUG] scheduler_loop error: {e}")

        time.sleep(60)


def start_scheduler():
    global _scheduler_started
    if _scheduler_started:
        logger.info("[VISITOR_MAIL_SCHED] already started, skipping")
        print("[DEBUG] start_scheduler called but already started")
        return

    _scheduler_started = True
    print("[DEBUG] starting scheduler thread")
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    logger.info("[VISITOR_MAIL_SCHED] background scheduler thread started")
    print("[DEBUG] scheduler thread started")