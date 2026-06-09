# cs_tasks/models.py
from datetime import time as dtime

from django.conf import settings
from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _l


def active_lang():
    """現在アクティブな表示言語を pick_lang 用に正規化する。

    日中トグル(switch_language)は translation.activate で言語を切り替えるため、
    get_language() が 'ja' / 'zh-hans' を返す。pick_lang は lang=='ja' を見るので、
    'ja' 始まりを 'ja'、それ以外(中国語)を 'zh' に畳む。
    """
    return "ja" if (get_language() or "").lower().startswith("ja") else "zh"


def pick_lang(primary, ja, lang):
    """言語に応じて表示文言を選ぶ（双方向フォールバック）。

    - lang == 'ja': ja が非空なら ja、空なら primary(中文) にフォールバック
    - lang == 'zh' その他: primary が非空なら primary、空なら ja にフォールバック

    双方向フォールバックの理由:
      社内側は両言語入力可能で、入力時に「検出言語側」のフィールドだけ埋め、
      逆側は空のままにする（Mac 側翻訳ワークフローへのシグナル）。
      Mac が未翻訳の間も、ユーザーには「ある方の言語」で表示する必要がある。
    """
    if lang == "ja" and (ja or "").strip():
        return ja
    if (primary or "").strip():
        return primary
    return ja or ""


class Task(models.Model):
    # 区分（サブナビのタブ）: クレーム・インシデント / 既存顧客課題 / 新規顧客課題 / 部内課題
    CATEGORY_EXISTING = "existing"
    CATEGORY_NEW = "new"
    CATEGORY_INTERNAL = "internal"
    CATEGORY_INCIDENT = "incident"
    CATEGORY_CHOICES = [
        (CATEGORY_EXISTING, "既存顧客課題"),
        (CATEGORY_NEW, "新規顧客課題"),
        (CATEGORY_INTERNAL, "部内課題"),
        (CATEGORY_INCIDENT, "クレーム・インシデント (Bad News First)"),
    ]
    category = models.CharField(
        verbose_name=_l("区分"), max_length=16,
        choices=CATEGORY_CHOICES, default=CATEGORY_EXISTING,
    )

    title = models.CharField(verbose_name=_l("課題名"), max_length=255)
    title_ja = models.CharField(
        verbose_name=_l("課題名(日本語)"), max_length=255, blank=True
    )
    description = models.TextField(verbose_name=_l("詳細"), blank=True)
    description_ja = models.TextField(verbose_name=_l("詳細(日本語)"), blank=True)
    client_name = models.CharField(verbose_name=_l("客先名"), max_length=255, blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_tasks",
        verbose_name=_l("登録者"),
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
        verbose_name=_l("担当者"),
    )

    due_date = models.DateField(verbose_name=_l("期限"), null=True, blank=True)

    # 完了（クローズ）: 上長のみが操作
    is_closed = models.BooleanField(verbose_name=_l("完了"), default=False)
    completed_at = models.DateTimeField(verbose_name=_l("完了日時"), null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_tasks",
        verbose_name=_l("完了操作者"),
    )

    # 中止（論理削除）
    is_cancelled = models.BooleanField(verbose_name=_l("中止"), default=False)
    cancelled_at = models.DateTimeField(verbose_name=_l("中止日時"), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_l("作成日時"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_l("更新日時"))

    class Meta:
        ordering = ["-created_at", "id"]
        verbose_name = _l("CS課題")
        verbose_name_plural = _l("CS課題")

    def __str__(self):
        return f"{self.title}（{self.client_name}）" if self.client_name else self.title

    def display_title(self, lang=None):
        return pick_lang(self.title, self.title_ja, lang or active_lang())

    def display_description(self, lang=None):
        return pick_lang(self.description, self.description_ja, lang or active_lang())


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
    content_ja = models.TextField(verbose_name="進捗内容(日本語)", blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="記入日時")
    # 実施日（記入忘れで後日入れた場合に実際の実施日を選べる。未設定なら記入日を使う）
    execution_date = models.DateField(verbose_name="実施日", null=True, blank=True)

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

    def display_content(self, lang=None):
        return pick_lang(self.content, self.content_ja, lang or active_lang())

    @property
    def effective_date(self):
        """表示・編集用の実施日。未設定なら記入日(created_at)の日付にフォールバック。"""
        if self.execution_date:
            return self.execution_date
        return self.created_at.date() if self.created_at else None


class SupervisorComment(models.Model):
    """上長指示・コメント。進捗1件に対し複数可（1:多）。付与は is_staff のみ。"""
    progress = models.ForeignKey(
        ProgressUpdate,
        on_delete=models.CASCADE,
        related_name="comments",
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
    content_ja = models.TextField(verbose_name="コメント(日本語)", blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="記入日時")

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "上長コメント"
        verbose_name_plural = "上長コメント"

    def __str__(self):
        return f"{self.progress_id}: {self.content[:20]}"

    def display_content(self, lang=None):
        return pick_lang(self.content, self.content_ja, lang or active_lang())


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


# =========================================================
# 翻訳ブリッジ: 復路(Mac→社内)受信処理の冪等性・リプレイ防止
# =========================================================
class BridgeProcessedMessage(models.Model):
    """処理済みの書き戻しメール(nonce単位)。同一メールの再適用を防ぐ。

    監査用に受信した [CS-WB] 本文原文と差出人も保存する（メール削除後も追跡可能に）。
    """
    nonce = models.CharField(verbose_name="メッセージnonce", max_length=128, unique=True)
    sender = models.CharField(verbose_name="差出人", max_length=320, blank=True, default="")
    raw_body = models.TextField(verbose_name="受信本文(原文)", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="処理日時")

    class Meta:
        verbose_name = "ブリッジ処理済みメッセージ"
        verbose_name_plural = "ブリッジ処理済みメッセージ"

    def __str__(self):
        return self.nonce


class BridgeProcessedOperation(models.Model):
    """処理済みの個別操作(op_id単位)。同一操作の二重適用を防ぐ。"""
    op_id = models.CharField(verbose_name="操作ID", max_length=128, unique=True)
    action = models.CharField(verbose_name="操作種別", max_length=40)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="適用日時")
    # add_task が生成した Task の id を記録（後続 op の task_ref 解決に使う。
    # 別メールで先に課題追加→後から進捗追加が来ても実IDに紐付けられる）
    result_task_id = models.IntegerField(verbose_name="生成課題ID", null=True, blank=True)

    class Meta:
        verbose_name = "ブリッジ処理済み操作"
        verbose_name_plural = "ブリッジ処理済み操作"

    def __str__(self):
        return f"{self.action}:{self.op_id}"
