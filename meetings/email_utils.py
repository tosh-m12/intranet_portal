# meetings/email_utils.py
from datetime import date
import logging

from django.template.loader import render_to_string

from .models import (
    Meeting,
    MeetingMailRecipient,
)
from working_schedule.utils import is_holiday
from mailcenter.email_utils import send_html_mail

logger = logging.getLogger(__name__)


def get_recipients():
    """
    MeetingMailRecipient モデルからメーリングリストを取得
    """
    recipients = list(MeetingMailRecipient.objects.values_list("email", flat=True))
    # 空文字などを除外
    return [r for r in recipients if r]


def get_meetings_for_mail(today: date):
    """
    Meeting モデルから、本日以降の訪問・WEB会議予定を取得して、
    メールテンプレートで使いやすい dict のリストに整形する。
    """
    qs = Meeting.objects.filter(visit_date__gte=today).order_by(
        "visit_date", "visit_time", "id"
    )

    meetings = []
    for m in qs:
        meetings.append(
            {
                "id": m.id,
                "visit_date": m.visit_date,
                "visit_time": m.visit_time.strftime("%H:%M") if m.visit_time else "",
                "time_undecided_flag": m.time_undecided,
                "company_name": m.company_name,
                "last_name": m.last_name,
                "first_name": m.first_name,
                "title": m.title,
                "purpose": m.purpose,
                "location": m.location,
                "host_staff": m.host_staff,
                "is_cancelled": m.cancelled,
                "cancelled": "true" if m.cancelled else "false",
            }
        )

    return meetings


def send_daily_email(ignore_holiday: bool = False):
    """
    本日以降の訪問・WEB会議予定一覧を、メーリングリスト宛に HTML メールで送信する。

    ignore_holiday:
        True  の場合 → 休日判定を無視して送信する（手動送信用）
        False の場合 → 休日は送信しない（自動送信用：デフォルト）
    """
    today = date.today()
    logger.info(
        f"[MEETING_MAIL] send_daily_email start: today={today}, ignore_holiday={ignore_holiday}"
    )
    result = {
        "sent": False,
        "reason": "",
        "recipients": [],
        "meeting_count": 0,
    }

    # 1) 休日判定（自動送信のときだけ有効）
    if (not ignore_holiday) and is_holiday(today):
        msg = "今日は休日のため送信しません。"
        logger.info(f"[MEETING_MAIL] {msg}")
        result["reason"] = msg
        return result

    # 2) 宛先取得
    recipients = get_recipients()
    result["recipients"] = recipients

    if not recipients:
        msg = "メーリングリストが空のため送信しません。"
        logger.warning(f"[MEETING_MAIL] {msg}")
        result["reason"] = msg
        return result

    # 3) 訪問・WEB会議予定取得
    meetings = get_meetings_for_mail(today)
    result["meeting_count"] = len(meetings)

    logger.info(
        f"[MEETING_MAIL] meeting_count={len(meetings)}, recipients={recipients}"
    )

    # 4) HTML本文生成
    context = {
        "meetings": meetings,
        "today": today,
    }
    html_content = render_to_string("meetings/email_template.html", context)

    # 5) 共通メールセンター経由で送信
    subject = "【訪問・WEB会議予定通知】本日以降の予定一覧"
    send_res = send_html_mail(
        subject=subject,
        html_body=html_content,
        recipients=recipients,
        account_code="meeting_notice",  # MailAccount.code に合わせて定義しておく
    )

    if not send_res.get("sent"):
        result["reason"] = send_res.get("reason", "unknown error")
        logger.warning(f"[MEETING_MAIL] send_html_mail failed: {result['reason']}")
        return result

    logger.info(f"[MEETING_MAIL] メール送信完了: {len(recipients)}件 → {recipients}")
    result["sent"] = True
    return result
