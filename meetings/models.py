# meetings/models.py
from django.db import models
from django.conf import settings


class Meeting(models.Model):
    visit_date = models.DateField(verbose_name="訪問日")
    visit_time = models.TimeField(verbose_name="時間", null=True, blank=True)
    time_undecided = models.BooleanField(verbose_name="時間未定", default=False)

    company_name = models.CharField(verbose_name="正式会社名", max_length=255)
    last_name = models.CharField(verbose_name="姓", max_length=100)
    first_name = models.CharField(verbose_name="名", max_length=100)
    title = models.CharField(verbose_name="役職", max_length=100, blank=True)
    purpose = models.CharField(verbose_name="目的", max_length=255, blank=True)

    # 「訪問」「WEB会議」などを入れる
    location = models.CharField(verbose_name="訪問・WEB", max_length=255)

    # 画面表示用の入力者名（姓 名 or メールアドレスなど）
    host_staff = models.CharField(verbose_name="入力者", max_length=255)

    # 将来 WEB 会議用の URL 等を足しても良い
    cancelled = models.BooleanField(verbose_name="キャンセル", default=False)

    # visitors と同じ思想：権限判定用のユーザー
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,      # 既存データ用に許可
        blank=True,     # admin画面で空でも保存可能
        on_delete=models.SET_NULL,   # ユーザー削除時も Meeting は残す
        related_name="created_meetings",
        verbose_name="入力者ユーザー",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="作成日時")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新日時")

    class Meta:
        ordering = ["visit_date", "visit_time", "id"]

    def __str__(self):
        return f"{self.visit_date} {self.company_name} {self.last_name}{self.first_name}"


class MeetingMailRecipient(models.Model):
    """
    visitors.MailingAddress と同じ役割：
    meetings 用のメーリングリスト。
    """
    email = models.EmailField(unique=True)

    def __str__(self):
        return self.email


class MeetingMailConfig(models.Model):
    """
    訪問・WEB会議予定メールの設定。

    visitors.VisitMailConfig から簡略版を作成：
      - SMTP 項目は最初から持たない
      - スケジューラ方式は基本 Django 内部 or 自動送信なし
    """

    MODE_DJANGO = "django"
    MODE_NONE = "none"

    MODE_CHOICES = [
        (MODE_DJANGO, "Django 内部スケジューラで送信"),
        (MODE_NONE, "自動送信なし（手動のみ）"),
    ]

    send_time = models.TimeField(
        verbose_name="送信時刻（毎日）",
        default="09:00",
    )

    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=MODE_DJANGO,
        verbose_name="スケジューラ方式",
    )

    last_sent_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="最終送信日",
    )

    def __str__(self):
        return f"毎日 {self.send_time.strftime('%H:%M')} / mode={self.mode}"
