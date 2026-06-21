# cs_tasks/models.py
from datetime import time as dtime

from django.conf import settings
from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _


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
        verbose_name=_("区分"), max_length=16,
        choices=CATEGORY_CHOICES, default=CATEGORY_EXISTING,
    )

    title = models.CharField(verbose_name=_("課題名"), max_length=255)
    title_ja = models.CharField(
        verbose_name=_("課題名(日本語)"), max_length=255, blank=True
    )
    description = models.TextField(verbose_name=_("詳細"), blank=True)
    description_ja = models.TextField(verbose_name=_("詳細(日本語)"), blank=True)
    client_name = models.CharField(verbose_name=_("客先名"), max_length=255, blank=True)

    # ===== ビジネス概要（新規顧客課題のみ。タイトルと詳細の間に表示・編集） =====
    # 翻訳対象外の構造化データ（単一値）。空＝未入力。
    BIZ_STATUS_CHOICES = [
        ("negotiating", _("交渉中")),
        ("won", _("受注")),
        ("lost", _("失注")),
    ]
    REVENUE_TYPE_CHOICES = [
        ("recurring", _("継続")),
        ("spot", _("スポット")),
    ]
    BIZ_TYPE_CHOICES = [
        ("import", _("輸入型")),
        ("export", _("輸出型")),
        ("zone", _("園区遊")),
        ("triangle", _("3国間")),
        ("other", _("その他")),
    ]
    biz_status = models.CharField(
        verbose_name=_("状態"), max_length=16, choices=BIZ_STATUS_CHOICES, blank=True
    )
    # スタート時期: 月初日で保持（年月のみ使用）。未定は start_undecided=True。
    start_month = models.DateField(verbose_name=_("スタート時期"), null=True, blank=True)
    start_undecided = models.BooleanField(verbose_name=_("スタート時期未定"), default=False)
    revenue_type = models.CharField(
        verbose_name=_("継続・スポット"), max_length=16, choices=REVENUE_TYPE_CHOICES, blank=True
    )
    # 予想売上(人民元CNY)。単位(/月 or /次)は revenue_type から導出する。
    expected_revenue = models.DecimalField(
        verbose_name=_("予想売上(CNY)"), max_digits=14, decimal_places=2, null=True, blank=True
    )
    biz_type = models.CharField(
        verbose_name=_("ビジネス形態"), max_length=16, choices=BIZ_TYPE_CHOICES, blank=True
    )
    biz_type_other = models.CharField(
        verbose_name=_("ビジネス形態(その他)"), max_length=100, blank=True
    )
    group_contact = models.CharField(
        verbose_name=_("グループ内客先窓口"), max_length=255, blank=True
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_tasks",
        verbose_name=_("登録者"),
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
        verbose_name=_("担当者"),
    )

    due_date = models.DateField(verbose_name=_("期限"), null=True, blank=True)

    # 完了（クローズ）: 上長のみが操作
    is_closed = models.BooleanField(verbose_name=_("完了"), default=False)
    completed_at = models.DateTimeField(verbose_name=_("完了日時"), null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_tasks",
        verbose_name=_("完了操作者"),
    )

    # 非表示（論理削除）: 課題管理表の責任者が「本当に終わった案件」と確認し、一覧から
    # 消した状態。物理削除ではなく DB 行は保持する（社内の一覧からは消えるが、責任者の
    # Mac 管理コンソールから完全削除＝物理削除する場合は bridge の purge op で行う）。
    is_hidden = models.BooleanField(verbose_name=_("非表示"), default=False)
    hidden_at = models.DateTimeField(verbose_name=_("非表示日時"), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("作成日時"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("更新日時"))

    class Meta:
        ordering = ["-created_at", "id"]
        verbose_name = _("CS課題")
        verbose_name_plural = _("CS課題")

    def __str__(self):
        return f"{self.title}（{self.client_name}）" if self.client_name else self.title

    def display_title(self, lang=None):
        return pick_lang(self.title, self.title_ja, lang or active_lang())

    def display_description(self, lang=None):
        return pick_lang(self.description, self.description_ja, lang or active_lang())

    # ===== ビジネス概要の表示用ヘルパ =====
    @property
    def revenue_unit(self):
        """予想売上の単位。スポット=/次、それ以外(継続・未選択)は既定で /月。"""
        return "/次" if self.revenue_type == "spot" else "/月"

    @property
    def expected_revenue_display(self):
        """カンマ区切り・小数2位。未入力は空。"""
        if self.expected_revenue is None:
            return ""
        return f"{self.expected_revenue:,.2f}"

    @property
    def biz_type_label(self):
        """ビジネス形態の表示名。その他は記入テキストを優先。"""
        if self.biz_type == "other":
            return self.biz_type_other or self.get_biz_type_display()
        return self.get_biz_type_display() if self.biz_type else ""


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
    subject = models.CharField(
        verbose_name="メール件名(日本語)",
        max_length=255,
        blank=True,
        default="CS課題 週次レポート",
    )
    body = models.TextField(
        verbose_name="メール本文(日本語)",
        blank=True,
        default=(
            "お疲れ様です。\n"
            "今週の CS 課題レポートを送付します。ご確認をお願いいたします。"
        ),
        help_text="この本文の下に、レポートの課題表が自動で挿入されます。",
    )
    # 中文版(Mac側で件名・本文を翻訳して書き戻す)。日本語版とは別便で送信。
    subject_zh = models.CharField(
        verbose_name="メール件名(中文)",
        max_length=255,
        blank=True,
        default="",
    )
    body_zh = models.TextField(
        verbose_name="メール本文(中文)",
        blank=True,
        default="",
    )
    # 自動翻訳連携: 件名・本文をどちらの言語で編集したか(翻訳元)と、相手言語へ反映済みか。
    # 本番で編集すると translated=False になり、Mac のバックグラウンド翻訳が拾って相手言語へ
    # 翻訳→書き戻し(translated=True)する。
    source_lang = models.CharField(
        verbose_name="翻訳元言語",
        max_length=8,
        default="ja",
    )
    translated = models.BooleanField(
        verbose_name="相手言語へ翻訳済み",
        default=True,
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
