from django.db import models


class OpsProcessedMessage(models.Model):
    """処理済み writeback リクエスト(nonce単位)。同一リクエストの再適用を防ぐ。"""
    nonce = models.CharField(verbose_name="リクエストnonce", max_length=128, unique=True)
    raw_body = models.TextField(verbose_name="受信本文(原文)", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="処理日時")

    class Meta:
        verbose_name = "汎用API処理済みリクエスト"
        verbose_name_plural = "汎用API処理済みリクエスト"

    def __str__(self):
        return self.nonce


class OpsAuditLog(models.Model):
    """writeback の1レコード更新ごとの監査記録(before/after)。dry_run は記録しない。"""
    model_label = models.CharField(verbose_name="モデル", max_length=100)
    # 注: Django の instance.pk と衝突するためフィールド名は target_pk とする。
    target_pk = models.CharField(verbose_name="対象pk", max_length=64)
    action = models.CharField(verbose_name="操作種別", max_length=40, default="update")
    before_json = models.JSONField(verbose_name="更新前", default=dict, blank=True)
    after_json = models.JSONField(verbose_name="更新後", default=dict, blank=True)
    actor = models.CharField(verbose_name="操作主体", max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="記録日時")

    class Meta:
        verbose_name = "汎用API監査ログ"
        verbose_name_plural = "汎用API監査ログ"
        ordering = ["-created_at", "id"]

    def __str__(self):
        return f"{self.model_label}#{self.target_pk} {self.action} @{self.created_at:%Y-%m-%d %H:%M}"
