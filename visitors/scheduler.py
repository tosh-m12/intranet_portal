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

    判定は DB 上の VisitMailConfig.last_sent_date を使用するので、
    再起動しても「今日すでに送ったかどうか」を正しく判定できる。
    """
    logger.info("[VISITOR_MAIL_SCHED] scheduler_loop started")
    print("[DEBUG] scheduler_loop started")

    while True:
        try:
            now = timezone.localtime()
            today = now.date()

            # 設定レコードを取得（なければ作成）
            config, _ = VisitMailConfig.objects.get_or_create(pk=1)

            send_time = config.send_time or dtime(9, 0)
            mode = config.mode or VisitMailConfig.MODE_WINDOWS

            # デバッグ表示
            print(
                f"[DEBUG] scheduler tick now={now}, mode={mode}, "
                f"send_time={send_time}, last_sent_date={config.last_sent_date}"
            )

            # Django内部スケジューラ以外のモードなら何もしない
            if mode != VisitMailConfig.MODE_DJANGO:
                # ここで last_sent_date をリセットしないのがポイント
                time.sleep(60)
                continue

            # 今日の send_time を表す datetime
            target = now.replace(
                hour=send_time.hour,
                minute=send_time.minute,
                second=0,
                microsecond=0,
            )

            # 条件:
            # 1. 現在時刻が send_time を過ぎている（>=）
            # 2. DB 上で今日まだ自動送信していない（last_sent_date != today）
            if now >= target and config.last_sent_date != today:
                logger.info(
                    f"[VISITOR_MAIL_SCHED] time reached: now={now}, "
                    f"send_time={send_time}, calling send_daily_email()"
                )
                print("[DEBUG] calling send_daily_email() from scheduler_loop")

                result = send_daily_email()

                # 今日送ったことを DB に記録
                config.last_sent_date = today
                config.save(update_fields=["last_sent_date"])

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
