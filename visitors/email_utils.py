import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date
import logging

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

    戻り値: dict
        {
            "sent": True/False,
            "reason": "送らなかった理由 or エラー内容",
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

    # 4) HTML本文生成
    context = {
        'visitors': visitors,
        'today': today,
    }
    html_content = render_to_string('visitors/email_template.html', context)

    # 5) VisitMailConfig から SMTP 設定を取得
    config, _ = VisitMailConfig.objects.get_or_create(pk=1)
    smtp_host = config.smtp_host or "smtp.qiye.aliyun.com"
    smtp_port = config.smtp_port or 587
    use_tls = config.use_tls
    use_ssl = config.use_ssl
    smtp_user = (config.smtp_user or "").strip()
    smtp_password = (config.smtp_password or "").strip()
    from_name = config.from_name or "NGLS-CS-INFO"

    # From ヘッダ（表示名 <メールアドレス>）
    if not smtp_user or not smtp_password:
        msg = "SMTPユーザーまたはパスワードが未設定のため送信しません。"
        logger.warning(f"[VISITOR_MAIL] {msg}")
        result["reason"] = msg
        return result

    # From ヘッダ（表示名 <メールアドレス>）
    from_addr = f"{from_name} <{smtp_user}>"
    # ★ エンベロープFrom（SMTPレベルの送信元）
    envelope_from = smtp_user

    msg = MIMEMultipart('alternative')
    msg['Subject'] = '【来訪予定通知】本日以降の来訪一覧'
    msg['From'] = from_addr
    msg['To'] = ", ".join(recipients)

    part = MIMEText(html_content, 'html', 'utf-8')
    msg.attach(part)

    # 6) SMTP 送信
    try:
        logger.info(
            f"[VISITOR_MAIL] SMTP 接続開始 host={smtp_host} port={smtp_port} "
            f"tls={use_tls} ssl={use_ssl}"
        )

        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)

        server.ehlo()
        if use_tls and not use_ssl:
            server.starttls()
            server.ehlo()

        # ★ ここは必須（すでにOK）
        server.login(smtp_user, smtp_password)

        # ★ 修正ポイント: envelope_from を使う
        server.sendmail(envelope_from, recipients, msg.as_string())
        server.quit()

        logger.info(f"[VISITOR_MAIL] メール送信完了: {len(recipients)}件 → {recipients}")
        result["sent"] = True
        return result

    except Exception as e:
        logger.error(f"[VISITOR_MAIL] メール送信エラー: {e}", exc_info=True)
        result["reason"] = f"SMTPエラー: {e}"
        return result
    