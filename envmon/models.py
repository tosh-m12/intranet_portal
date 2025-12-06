# envmon/models.py
from django.db import models
from datetime import time


class Location(models.Model):
    """
    倉庫ロケーション情報（外気測定ポイントも含む）
    例:
      code: "loc01", "loc02", "external_loc02" など
      name: "B倉庫FMC", "B倉庫湿度管理室前方 外気" など
    """
    code = models.CharField("ロケーションID", max_length=50, unique=True)
    name = models.CharField("倉庫名", max_length=255)
    is_external = models.BooleanField("外気測定用か", default=False)

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "ロケーション"
        verbose_name_plural = "ロケーション"

    def __str__(self) -> str:
        return f"{self.code} / {self.name}"


class DeviceAssignment(models.Model):
    """
    デバイスID → 現在割当ロケーション
    """
    device_id = models.CharField("デバイスID（シリアル）", max_length=100, unique=True)
    location = models.ForeignKey(
        Location,
        verbose_name="割当ロケーション",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "デバイス割当"
        verbose_name_plural = "デバイス割当"

    def __str__(self) -> str:
        return f"{self.device_id} -> {self.location}"


class AssignmentHistory(models.Model):
    """
    デバイス割当履歴（device_assignments.json を書き換えるたびに記録していたもの）
    """
    device_id = models.CharField("デバイスID（シリアル）", max_length=100)
    location = models.ForeignKey(
        Location,
        verbose_name="割当ロケーション",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    changed_at = models.DateTimeField("割当変更日時")

    class Meta:
        verbose_name = "割当履歴"
        verbose_name_plural = "割当履歴"
        indexes = [
            models.Index(fields=["device_id", "changed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.device_id} @ {self.location} ({self.changed_at})"


class EnvSettings(models.Model):
    """
    システム全体の設定（1レコードのみ想定）
    JSON の settings.json に相当
    """
    interval = models.IntegerField("表示更新間隔（秒）", default=10)
    cache_interval = models.IntegerField("キャッシュ取得間隔（秒）", default=300)
    cache_expire_hours = models.IntegerField("キャッシュ保存期間（時間）", default=168)
    log_directory = models.CharField("ログ保存先ディレクトリ", max_length=255, default="logs")

    # Django 5.2 + MySQL 8 であれば JSONField 使用可
    log_times = models.JSONField("ログ取得時刻リスト", default=list)

    # 履歴取得の時刻（1日1回）
    history_fetch_time = models.TimeField(
        "履歴データ取得時刻（1日1回）",
        default=time(1, 0),  # 初期値 01:00
    )

    # 追加：履歴自動取得の実行中フラグ
    is_fetching_history = models.BooleanField(
        "履歴データ取得中フラグ",
        default=False,
    )

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "温湿度設定"
        verbose_name_plural = "温湿度設定"

    def __str__(self) -> str:
        return "EnvSettings"

    @classmethod
    def get_solo(cls) -> "EnvSettings":
        """
        常に1件だけ存在する前提で、それを取得 or 作成するヘルパー。
        """
        obj, _ = cls.objects.get_or_create(id=1)
        return obj


class DeviceHistory(models.Model):
    """
    1weilian から取得した温湿度履歴の保存用テーブル。
    同じ (sn, recorded_at) のデータは重複しないよう UniqueConstraint で制御。
    """
    sn = models.CharField("シリアルナンバー", max_length=64, db_index=True)
    recorded_at = models.DateTimeField("計測日時", db_index=True)
    temperature = models.FloatField("温度", null=True, blank=True)
    humidity = models.FloatField("湿度", null=True, blank=True)
    raw = models.JSONField("生データ(JSON)", null=True, blank=True)

    class Meta:
        ordering = ["sn", "recorded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sn", "recorded_at"],
                name="uniq_device_history_sn_recorded_at",
            )
        ]

    def __str__(self):
        return f"{self.sn} @ {self.recorded_at}"
    
