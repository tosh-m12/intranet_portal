# mailcenter/email_utils.py
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging

from .models import MailAccount

logger = logging.getLogger(__name__)


def send_html_mail(subject: str, html_body: str, recipients, account_code="default"):
    """
    HTMLメールを送信する共通関数。

    subject: 件名
    html_body: HTML本文
    recipients: 宛先リスト(list[str])
    account_code: MailAccount.code（用途別に選択）
    """
    if isinstance(recipients, str):
        recipients = [recipients]

    # アカウント取得
    account = (
        MailAccount.objects.filter(code=account_code).first()
        or MailAccount.objects.first()
    )

    if not account:
        msg = "[MAILCENTER] MailAccount が設定されていないため送信できません。"
        logger.error(msg)
        return {"sent": False, "reason": msg}

    smtp_host = account.smtp_host or "smtp.qiye.aliyun.com"
    smtp_port = account.smtp_port or 587
    use_tls = account.use_tls
    use_ssl = account.use_ssl
    smtp_user = (account.smtp_user or "").strip()
    smtp_password = (account.smtp_password or "").strip()
    from_name = account.from_name or "NGLS-CS-INFO"

    if not smtp_user or not smtp_password:
        msg = "[MAILCENTER] SMTPユーザーまたはパスワードが未設定のため送信しません。"
        logger.warning(msg)
        return {"sent": False, "reason": msg}

    from_addr = f"{from_name} <{smtp_user}>"
    envelope_from = smtp_user

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    part = MIMEText(html_body, "html", "utf-8")
    msg.attach(part)

    try:
        logger.info(
            f"[MAILCENTER] SMTP 接続開始 host={smtp_host} port={smtp_port} "
            f"tls={use_tls} ssl={use_ssl} account_code={account_code}"
        )

        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)

        server.ehlo()
        if use_tls and not use_ssl:
            server.starttls()
            server.ehlo()

        server.login(smtp_user, smtp_password)
        server.sendmail(envelope_from, recipients, msg.as_string())
        server.quit()

        logger.info(
            f"[MAILCENTER] メール送信完了: {len(recipients)} 件 → {recipients}"
        )
        return {"sent": True, "reason": ""}
    except Exception as e:
        logger.error(f"[MAILCENTER] メール送信エラー: {e}", exc_info=True)
        return {"sent": False, "reason": f"SMTPエラー: {e}"}
