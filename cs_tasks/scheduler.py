# cs_tasks/scheduler.py
import threading
import time
import datetime
import logging

from django.core.management import call_command
from django.utils import timezone
from django.db import close_old_connections

logger = logging.getLogger(__name__)

_scheduler_started = False  # 多重起動防止

# CS Bridge: 往路/復路の実行間隔と最終実行時刻(プロセス内モノトニック秒)
_BRIDGE_INTERVAL_SEC = 300  # 5分
_last_sync_at = None
_last_inbound_at = None


def _scheduler_loop():
    """
    60秒ごとに tick し、
    - mode == django
    - 今日が送信曜日
    - 送信時刻を過ぎている
    - 今日まだ送っていない（last_sent_date != today）
    を満たしたら週報を送信する。
    """
    logger.info("[CSTASKS_SCHED] scheduler loop start")
    print("### cs_tasks scheduler loop start")
    tz = timezone.get_current_timezone()

    while True:
        try:
            from .models import WeeklyReportConfig
            from .email_utils import send_weekly_report

            now = timezone.localtime()
            today = now.date()

            config, _ = WeeklyReportConfig.objects.get_or_create(pk=1)

            if config.mode != WeeklyReportConfig.MODE_DJANGO:
                # 自動送信なし
                pass
            else:
                send_time = config.send_time or datetime.time(18, 0)
                scheduled_dt = timezone.make_aware(
                    datetime.datetime.combine(today, send_time), tz
                )

                logger.info(
                    "[CSTASKS_SCHED] tick now=%s, weekday=%s(target=%s), "
                    "send_time=%s, last_sent_date=%s",
                    now, now.weekday(), config.send_weekday,
                    send_time, config.last_sent_date,
                )

                if (
                    now.weekday() == config.send_weekday
                    and now >= scheduled_dt
                    and config.last_sent_date != today
                ):
                    logger.info("[CSTASKS_SCHED] conditions met, sending weekly report")
                    res = send_weekly_report(ignore_schedule=False)
                    if res.get("sent"):
                        config.last_sent_date = today
                        config.save(update_fields=["last_sent_date"])
                        logger.info(
                            "[CSTASKS_SCHED] sent, last_sent_date=%s", today
                        )
                    else:
                        logger.warning(
                            "[CSTASKS_SCHED] NOT sent (reason=%s)", res.get("reason")
                        )

            # ===== CS Bridge: 5分毎に 往路/復路 を Waitress プロセス内で実行 =====
            # タスクスケジューラ起動の .bat に頼らず、Waitress 内スレッドで定期実行する。
            # 環境変数(CS_BRIDGE_*)は run_portal.bat 経由で Waitress プロセスに継承済み。
            global _last_sync_at, _last_inbound_at
            mono = time.monotonic()
            if _last_sync_at is None or (mono - _last_sync_at) >= _BRIDGE_INTERVAL_SEC:
                try:
                    print("### [CSBRIDGE_SCHED] running cs_sync_send")
                    call_command("cs_sync_send", "--minutes", "30")
                except Exception:
                    logger.exception("[CSBRIDGE_SCHED] cs_sync_send failed")
                finally:
                    _last_sync_at = mono
            if _last_inbound_at is None or (mono - _last_inbound_at) >= _BRIDGE_INTERVAL_SEC:
                try:
                    print("### [CSBRIDGE_SCHED] running cs_inbound_poll")
                    call_command("cs_inbound_poll")
                except Exception:
                    logger.exception("[CSBRIDGE_SCHED] cs_inbound_poll failed")
                finally:
                    _last_inbound_at = mono

        except Exception:
            logger.exception("[CSTASKS_SCHED] error in scheduler loop")
        finally:
            close_old_connections()

        time.sleep(60)


def start_scheduler():
    """Django 起動時に一度だけバックグラウンドスレッドを起動する。"""
    global _scheduler_started
    if _scheduler_started:
        logger.info("[CSTASKS_SCHED] scheduler already started, skipping")
        return

    _scheduler_started = True
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    logger.info("[CSTASKS_SCHED] scheduler thread started")
