# visitors/scheduler.py
import threading
import time
import datetime
import logging

from django.utils import timezone
from django.db import close_old_connections

from .models import VisitMailConfig
from .email_utils import send_daily_email

# ★ 追加：meetings 側
from meetings.models import MeetingMailConfig
from meetings.email_utils import send_daily_email as send_meeting_daily_email

logger = logging.getLogger(__name__)

_scheduler_started = False  # 多重起動防止用フラグ


def _scheduler_loop():
    """
    60秒ごとに tick し、
    - 今日の send_time を過ぎているか
    - かつ last_sent_date != 今日
    であれば send_daily_email() を実行する。

    visitors（来客予定） + meetings（訪問・WEB会議）の両方をここで処理。
    """
    global _scheduler_started
    logger.info("[VISITOR_SCHED] scheduler loop start")
    print("### visitors scheduler loop start")
    tz = timezone.get_current_timezone()

    while True:
        try:
            now = timezone.localtime()
            today = now.date()
            print(f"### tick now={now}")

            # ==============================
            # 1) visitors（来客予定）の送信
            # ==============================
            v_config, _ = VisitMailConfig.objects.get_or_create(pk=1)
            v_send_time = v_config.send_time or datetime.time(9, 0)

            v_scheduled_dt = timezone.make_aware(
                datetime.datetime.combine(today, v_send_time),
                tz
            )

            # ★ debug → info に変更
            logger.info(
                "[VISITOR_SCHED] tick now=%s, send_time=%s, last_sent_date=%s",
                now, v_send_time, v_config.last_sent_date
            )

            if now >= v_scheduled_dt and v_config.last_sent_date != today:
                logger.info("[VISITOR_SCHED] conditions met, calling send_daily_email()")

                v_result = send_daily_email()  # ignore_holiday=False（自動送信）

                v_sent = False
                v_reason = ""

                if isinstance(v_result, dict):
                    v_sent = v_result.get("sent", False)
                    v_reason = v_result.get("reason", "")
                else:
                    v_sent = (v_result == "ok")
                    if not v_sent:
                        v_reason = str(v_result)

                if v_sent:
                    v_config.last_sent_date = today
                    v_config.save(update_fields=["last_sent_date"])
                    logger.info(
                        "[VISITOR_SCHED] mail sent successfully, last_sent_date=%s",
                        today
                    )
                else:
                    logger.warning(
                        "[VISITOR_SCHED] mail NOT sent (reason=%s, result=%r)",
                        v_reason, v_result
                    )

            # ==============================
            # 2) meetings（訪問・WEB会議）の送信 ★追加
            # ==============================
            m_config, _ = MeetingMailConfig.objects.get_or_create(pk=1)
            m_send_time = m_config.send_time or datetime.time(9, 0)

            m_scheduled_dt = timezone.make_aware(
                datetime.datetime.combine(today, m_send_time),
                tz
            )

            # ★ debug → info に変更
            logger.info(
                "[MEETING_SCHED] tick now=%s, send_time=%s, last_sent_date=%s",
                now, m_send_time, m_config.last_sent_date
            )

            if now >= m_scheduled_dt and m_config.last_sent_date != today:
                logger.info("[MEETING_SCHED] conditions met, calling send_meeting_daily_email()")

                m_result = send_meeting_daily_email(ignore_holiday=False)

                m_sent = False
                m_reason = ""

                if isinstance(m_result, dict):
                    m_sent = m_result.get("sent", False)
                    m_reason = m_result.get("reason", "")
                else:
                    m_sent = (m_result == "ok")
                    if not m_sent:
                        m_reason = str(m_result)

                if m_sent:
                    m_config.last_sent_date = today
                    m_config.save(update_fields=["last_sent_date"])
                    logger.info(
                        "[MEETING_SCHED] mail sent successfully, last_sent_date=%s",
                        today
                    )
                else:
                    logger.warning(
                        "[MEETING_SCHED] mail NOT sent (reason=%s, result=%r)",
                        m_reason, m_result
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
