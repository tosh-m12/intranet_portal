from django.db import models


class Visitor(models.Model):
    visit_date = models.DateField(verbose_name="訪問日")
    visit_time = models.TimeField(verbose_name="時間", null=True, blank=True)
    time_undecided = models.BooleanField(verbose_name="時間未定", default=False)

    company_name = models.CharField(verbose_name="正式会社名", max_length=255)
    last_name = models.CharField(verbose_name="姓", max_length=100)
    first_name = models.CharField(verbose_name="名", max_length=100)
    title = models.CharField(verbose_name="役職", max_length=100, blank=True)
    purpose = models.CharField(verbose_name="目的", max_length=255, blank=True)
    location = models.CharField(verbose_name="訪問場所", max_length=255)
    host_staff = models.CharField(verbose_name="入力者", max_length=255)
    notes = models.TextField(verbose_name="備考", blank=True)

    cancelled = models.BooleanField(verbose_name="キャンセル", default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["visit_date", "visit_time", "id"]

    def __str__(self):
        return f"{self.visit_date} {self.company_name} {self.last_name}{self.first_name}"


class MailingAddress(models.Model):
    email = models.EmailField(unique=True)

    def __str__(self):
        return self.email


class HolidayDate(models.Model):
    date = models.DateField()


class VisitMailConfig(models.Model):
    MODE_WINDOWS = 'windows'
    MODE_DJANGO = 'django'
    MODE_NONE = 'none'

    MODE_CHOICES = [
        (MODE_WINDOWS, 'Windows タスクスケジューラで送信'),
        (MODE_DJANGO, 'Django 内部スケジューラで送信'),
        (MODE_NONE, '自動送信なし（手動のみ）'),
    ]

    send_time = models.TimeField(verbose_name="送信時刻（毎日）", default="09:00")
    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=MODE_WINDOWS,
        verbose_name="スケジューラ方式",
    )

    # ★ これを追加
    last_sent_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="最終送信日",
    )

    # ★ ここから追加：SMTP 設定
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
        return f"毎日 {self.send_time.strftime('%H:%M')} / mode={self.mode}"
