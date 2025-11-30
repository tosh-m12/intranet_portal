from datetime import date
import logging

from django.template.loader import render_to_string

from .models import Visitor, MailingAddress   # VisitMailConfig はここでは未使用
from working_schedule.utils import is_holiday
from mailcenter.email_utils import send_html_mail

logger = logging.getLogger(__name__)


def get_recipients():
    """MailingAddressモデルからメーリングリストを取得"""
    recipients = list(MailingAddress.objects.values_list("email", flat=True))
    return [r for r in recipients if r]


def get_visitors_for_mail(today: date):
    """
    Visitorモデルから、本日以降の来訪予定を取得して、
    メールテンプレートで使いやすい dict のリストに整形する。
    """
    qs = Visitor.objects.filter(visit_date__gte=today).order_by(
        "visit_date", "visit_time", "id"
    )

    visitors = []
    for v in qs:
        visitors.append(
            {
                "id": v.id,
                "visit_date": v.visit_date,
                "visit_time": v.visit_time.strftime("%H:%M") if v.visit_time else "",
                "time_undecided_flag": v.time_undecided,
                "company_name": v.company_name,
                "last_name": v.last_name,
                "first_name": v.first_name,
                "title": v.title,
                "purpose": v.purpose,
                "location": v.location,
                "host_staff": v.host_staff,
                "is_cancelled": v.cancelled,
                "cancelled": "true" if v.cancelled else "false",
            }
        )

    return visitors


def send_daily_email(ignore_holiday: bool = False):
    """
    本日以降の来訪予定一覧を、メーリングリスト宛にHTMLメールで送信する。

    ignore_holiday:
        True  の場合 → 休日判定を無視して送信する（手動送信用）
        False の場合 → 休日は送信しない（自動送信用：デフォルト）
    """
    today = date.today()
    logger.info(
        f"[VISITOR_MAIL] send_daily_email start: today={today}, ignore_holiday={ignore_holiday}"
    )
    result = {
        "sent": False,
        "reason": "",
        "recipients": [],
        "visitor_count": 0,
    }

    # 1) 休日判定（自動送信のときだけ有効）
    if (not ignore_holiday) and is_holiday(today):
        msg = "今日は休日のため送信しません。"
        logger.info(f"[VISITOR_MAIL] {msg}")
        result["reason"] = msg
        return result

    # 2) 宛先取得
    recipients = get_recipients()
    result["recipients"] = recipients

    if not recipients:
        msg = "メーリングリストが空のため送信しません。"
        logger.warning(f"[VISITOR_MAIL] {msg}")
        result["reason"] = msg
        return result

    # 3) 来訪予定取得
    visitors = get_visitors_for_mail(today)
    result["visitor_count"] = len(visitors)

    logger.info(
        f"[VISITOR_MAIL] visitor_count={len(visitors)}, recipients={recipients}"
    )

    # 4) HTML本文生成
    context = {
        "visitors": visitors,
        "today": today,
    }
    html_content = render_to_string("visitors/email_template.html", context)

    # 5) 共通メールセンター経由で送信
    subject = "【来訪予定通知】本日以降の来訪一覧"
    send_res = send_html_mail(
        subject=subject,
        html_body=html_content,
        recipients=recipients,
        account_code="visitor_notice",  # MailAccount.code と合わせる
    )

    if not send_res.get("sent"):
        result["reason"] = send_res.get("reason", "unknown error")
        logger.warning(f"[VISITOR_MAIL] send_html_mail failed: {result['reason']}")
        return result

    logger.info(f"[VISITOR_MAIL] メール送信完了: {len(recipients)}件 → {recipients}")
    result["sent"] = True
    return result
