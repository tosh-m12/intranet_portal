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
    location = models.CharField(verbose_name="訪問場所", max_length=255)
    host_staff = models.CharField(verbose_name="入力者", max_length=255)

    # 将来 WEB 会議用の URL 等を足しても良い
    cancelled = models.BooleanField(verbose_name="キャンセル", default=False)

    # ★ 追加（visitors と同じ思想）
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='meetings',
        null=True,   # 既存データ用に許可
        blank=True,  # admin 画面で空でも保存できるように
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["visit_date", "visit_time", "id"]

    def __str__(self):
        return f"{self.visit_date} {self.company_name} {self.last_name}{self.first_name}"
