# mailcenter/models.py
from django.db import models


class MailAccount(models.Model):
    """
    共通メール送信に使うSMTPアカウント。
    code で用途別に使い分け可能（例: 'visitor_notice', 'report_notice' など）
    """
    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="アカウントコード",
        help_text="例: visitor_notice, report_notice など",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="アカウント名（説明用）",
    )

    smtp_host = models.CharField(
        max_length=255,
        verbose_name="SMTPサーバー",
        default="smtp.qiye.aliyun.com",
    )
    smtp_port = models.IntegerField(
        verbose_name="ポート番号",
        default=587,
    )
    use_tls = models.BooleanField(
        verbose_name="TLS を使用",
        default=True,
    )
    use_ssl = models.BooleanField(
        verbose_name="SSL を使用",
        default=False,
    )
    smtp_user = models.EmailField(
        verbose_name="SMTPユーザー（ログインID / From）",
        blank=True,
    )
    smtp_password = models.CharField(
        verbose_name="SMTPパスワード",
        max_length=255,
        blank=True,
    )
    from_name = models.CharField(
        verbose_name="送信者名（表示名）",
        max_length=255,
        blank=True,
        default="NGLS-CS-INFO",
    )

    def __str__(self):
        return f"{self.code} ({self.name})"
