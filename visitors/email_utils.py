# visitors/email_utils.py

from datetime import date
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from .models import Visitor, MailingAddress, HolidayDate, VisitMailConfig

logger = logging.getLogger(__name__)


def is_holiday(today: date) -> bool:
    """Holidayモデルを使って本日が休日か判定"""
    return HolidayDate.objects.filter(date=today).exists()


def get_recipients():
    """MailingAddressモデルからメーリングリストを取得"""
    recipients = list(MailingAddress.objects.values_list('email', flat=True))
    return [r for r in recipients if r]


def get_visitors_for_mail(today: date):
    """
    Visitorモデルから、本日以降の来訪予定を取得して、
    メールテンプレートで使いやすい dict のリストに整形する。
    """
    qs = Visitor.objects.filter(visit_date__gte=today).order_by('visit_date', 'visit_time', 'id')

    visitors = []
    for v in qs:
        visitors.append({
            'id': v.id,
            'visit_date': v.visit_date,
            'visit_time': v.visit_time.strftime('%H:%M') if v.visit_time else '',
            'time_undecided_flag': v.time_undecided,
            'company_name': v.company_name,
            'last_name': v.last_name,
            'first_name': v.first_name,
            'title': v.title,
            'purpose': v.purpose,
            'location': v.location,
            'host_staff': v.host_staff,
            'notes': v.notes,
            'is_cancelled': v.cancelled,
            'cancelled': 'true' if v.cancelled else 'false',
        })

    return visitors


def send_daily_email():
    """
    本日以降の来訪予定一覧を、メーリングリスト宛にHTMLメールで送信する。

    - 休日なら送信しない
    - メーリングリストが空なら送信しない
    - 戻り値で送信有無・件数・理由を返す

    戻り値: dict
        {
            "sent": True/False,
            "reason": "送らなかった理由" or "",
            "recipients": [...],
            "visitor_count": int,
        }
    """
    today = date.today()
    logger.info(f"[VISITOR_MAIL] send_daily_email start: today={today}")
    result = {
        "sent": False,
        "reason": "",
        "recipients": [],
        "visitor_count": 0,
    }

    # 1) 休日判定
    if is_holiday(today):
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

    logger.info(f"[VISITOR_MAIL] visitor_count={len(visitors)}, recipients={recipients}")

    # 4) HTML本文生成（旧 email_template.html を利用）
    context = {
        'visitors': visitors,
        'today': today,
    }
    html_content = render_to_string('visitors/email_template.html', context)

    # 5) 送信時刻設定（VisitMailConfig は「何時に送るか」の設定テーブル）
    config, _ = VisitMailConfig.objects.get_or_create(pk=1)
    send_time_str = config.send_time.strftime('%H:%M')

    subject = f'【来訪予定通知】本日以降の来訪一覧（送信時刻設定: {send_time_str}）'
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or settings.EMAIL_HOST_USER

    # 6) EmailMessage で HTML 送信
    msg = EmailMessage(
        subject=subject,
        body=html_content,
        from_email=from_email,
        to=recipients,
    )
    msg.content_subtype = 'html'  # HTMLメールとして送信

    logger.info(f"[VISITOR_MAIL] sending mail: subject={subject}, from={from_email}, to={recipients}")
    sent_count = msg.send(fail_silently=False)
    logger.info(f"[VISITOR_MAIL] send finished: sent_count={sent_count}")

    result["sent"] = sent_count > 0
    if not result["sent"]:
        result["reason"] = "メール送信はエラーなく終了しましたが、sent_count=0 でした。"

    return result
