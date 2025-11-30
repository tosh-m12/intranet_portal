# visitors/scheduler.py
import threading
import time
import datetime
import logging

from django.utils import timezone
from django.db import close_old_connections

from .models import VisitMailConfig
from .email_utils import send_daily_email

logger = logging.getLogger(__name__)

_scheduler_started = False  # 多重起動防止用フラグ


def _scheduler_loop():
    """
    60秒ごとに tick し、
    - 今日の send_time を過ぎているか
    - かつ last_sent_date != 今日
    であれば send_daily_email() を実行する。
    """
    global _scheduler_started
    logger.info("[VISITOR_SCHED] scheduler loop start")
    tz = timezone.get_current_timezone()

    while True:
        try:
            now = timezone.localtime()
            today = now.date()

            config, _ = VisitMailConfig.objects.get_or_create(pk=1)
            send_time = config.send_time or datetime.time(9, 0)

            # 今日の「送信予定日時（aware）」を作る
            scheduled_dt = timezone.make_aware(
                datetime.datetime.combine(today, send_time),
                tz
            )

            logger.debug(
                "[VISITOR_SCHED] tick now=%s, send_time=%s, last_sent_date=%s",
                now, send_time, config.last_sent_date
            )

            # まだ送っておらず、かつ予定時刻を過ぎている場合に送信
            if now >= scheduled_dt and config.last_sent_date != today:
                logger.info("[VISITOR_SCHED] conditions met, calling send_daily_email()")

                result = send_daily_email()  # ignore_holiday=False（デフォルト）

                sent = False
                reason = ""

                if isinstance(result, dict):
                    sent = result.get("sent", False)
                    reason = result.get("reason", "")
                else:
                    # 万が一古い仕様が返ってきても一応対応
                    sent = (result == "ok")
                    if not sent:
                        reason = str(result)

                if sent:
                    config.last_sent_date = today
                    config.save(update_fields=["last_sent_date"])
                    logger.info(
                        "[VISITOR_SCHED] mail sent successfully, last_sent_date=%s",
                        today
                    )
                else:
                    logger.warning(
                        "[VISITOR_SCHED] mail NOT sent (reason=%s, result=%r)",
                        reason, result
                    )

        except Exception:
            logger.exception("[VISITOR_SCHED] error in scheduler loop")
        finally:
            # DBコネクションを閉じてリーク防止
            close_old_connections()

        # 1分おきにチェック
        time.sleep(60)


def start_scheduler():
    """
    Django 起動時に一度だけ呼び出して、バックグラウンドスレッドを起動する。
    """
    global _scheduler_started
    if _scheduler_started:
        logger.info("[VISITOR_SCHED] scheduler already started, skipping")
        return

    _scheduler_started = True
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    logger.info("[VISITOR_SCHED] scheduler thread started")
