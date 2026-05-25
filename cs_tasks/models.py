# cs_tasks/models.py
from datetime import time as dtime

from django.conf import settings
from django.db import models


class Task(models.Model):
    title = models.CharField(verbose_name="課題名", max_length=255)
    description = models.TextField(verbose_name="詳細", blank=True)
    client_name = models.CharField(verbose_name="客先名", max_length=255, blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_tasks",
        verbose_name="登録者",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
        verbose_name="担当者",
    )

    due_date = models.DateField(verbose_name="期限", null=True, blank=True)

    # 完了（クローズ）: 上長のみが操作
    is_closed = models.BooleanField(verbose_name="完了", default=False)
    completed_at = models.DateTimeField(verbose_name="完了日時", null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_tasks",
        verbose_name="完了操作者",
    )

    # 中止（論理削除）
    is_cancelled = models.BooleanField(verbose_name="中止", default=False)
    cancelled_at = models.DateTimeField(verbose_name="中止日時", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="作成日時")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新日時")

    class Meta:
        ordering = ["-created_at", "id"]
        verbose_name = "CS課題"
        verbose_name_plural = "CS課題"

    def __str__(self):
        return f"{self.title}（{self.client_name}）" if self.client_name else self.title


class ProgressUpdate(models.Model):
    """進捗追記（時系列で履歴保持）。各行は個別にクローズ可能（上長のみ）。"""
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="progress_updates",
        verbose_name="課題",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cs_progress_updates",
        verbose_name="記入者",
    )
    content = models.TextField(verbose_name="進捗内容")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="記入日時")

    # 行単位のクローズ（上長のみ）
    is_closed = models.BooleanField(verbose_name="クローズ", default=False)
    closed_at = models.DateTimeField(verbose_name="クローズ日時", null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_progress_updates",
        verbose_name="クローズ操作者",
    )

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "進捗"
        verbose_name_plural = "進捗"

    def __str__(self):
        return f"{self.task_id}: {self.content[:20]}"


class SupervisorComment(models.Model):
    """上長指示・コメント。進捗1件に対し1件（1:1）。付与は is_staff のみ。"""
    progress = models.OneToOneField(
        ProgressUpdate,
        on_delete=models.CASCADE,
        related_name="comment",
        verbose_name="進捗",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cs_supervisor_comments",
        verbose_name="上長",
    )
    content = models.TextField(verbose_name="コメント")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="記入日時")

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "上長コメント"
        verbose_name_plural = "上長コメント"

    def __str__(self):
        return f"{self.progress_id}: {self.content[:20]}"


class WeeklyReportMailingList(models.Model):
    """週次レポートの宛先。"""
    name = models.CharField(verbose_name="表示名", max_length=255, blank=True)
    email = models.EmailField(verbose_name="メールアドレス", unique=True)
    is_active = models.BooleanField(verbose_name="有効", default=True)

    class Meta:
        ordering = ["name", "email"]
        verbose_name = "週報メーリングリスト"
        verbose_name_plural = "週報メーリングリスト"

    def __str__(self):
        return f"{self.name} <{self.email}>" if self.name else self.email


class WeeklyReportConfig(models.Model):
    """週次レポート送信スケジュール設定（単一レコード pk=1 を使う想定）。"""

    MODE_DJANGO = "django"
    MODE_NONE = "none"

    MODE_CHOICES = [
        (MODE_DJANGO, "Django 内部スケジューラで送信"),
        (MODE_NONE, "自動送信なし（手動のみ）"),
    ]

    WEEKDAY_CHOICES = [
        (0, "月曜"),
        (1, "火曜"),
        (2, "水曜"),
        (3, "木曜"),
        (4, "金曜"),
        (5, "土曜"),
        (6, "日曜"),
    ]

    send_time = models.TimeField(verbose_name="送信時刻", default=dtime(18, 0))
    send_weekday = models.IntegerField(
        verbose_name="送信曜日",
        choices=WEEKDAY_CHOICES,
        default=4,
    )
    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=MODE_DJANGO,
        verbose_name="スケジューラ方式",
    )
    last_sent_date = models.DateField(
        verbose_name="最終送信日",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "週報送信設定"
        verbose_name_plural = "週報送信設定"

    def __str__(self):
        weekday = dict(self.WEEKDAY_CHOICES).get(self.send_weekday, "")
        return f"毎週{weekday} {self.send_time.strftime('%H:%M')} / mode={self.mode}"
