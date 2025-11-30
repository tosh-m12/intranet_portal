# working_schedule/models.py
from django.db import models


class HolidayDate(models.Model):
    """
    土日以外の「休日」を登録するマスタ。
    例：春節・国慶節・年末年始など。
    """
    date = models.DateField(unique=True, verbose_name="休日日付")
    name = models.CharField(max_length=100, blank=True, verbose_name="名称（任意）")

    class Meta:
        verbose_name = "休日"
        verbose_name_plural = "休日マスタ"
        ordering = ["date"]

    def __str__(self):
        return f"{self.date} {self.name or ''}"


class SpecialWorkingDay(models.Model):
    """
    本来は休み（例：土日 or 祝日）だが、
    「特別に稼働する日」を登録するマスタ。
    例：振替出勤日など。
    """
    date = models.DateField(unique=True, verbose_name="特別稼働日")
    name = models.CharField(max_length=100, blank=True, verbose_name="名称（任意）")

    class Meta:
        verbose_name = "特別稼働日"
        verbose_name_plural = "特別稼働日マスタ"
        ordering = ["date"]

    def __str__(self):
        return f"{self.date} {self.name or ''}"
