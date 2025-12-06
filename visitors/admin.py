from django.contrib import admin
from .models import Visitor, MailingAddress, VisitMailConfig

@admin.register(Visitor)
class VisitorAdmin(admin.ModelAdmin):
    list_display = (
        "visit_date",
        "visit_time",
        "company_name",
        "last_name",
        "first_name",
        "location",
        "host_staff",
        "created_by",   # ← 入力者
        "cancelled",
    )

    fields = (
        "visit_date",
        "visit_time",
        "time_undecided",
        "company_name",
        "last_name",
        "first_name",
        "title",
        "purpose",
        "location",
        "host_staff",
        "created_by",   # ← 管理画面で編集可能にする
        "cancelled",
        "created_at",
        "updated_at",
    )

    readonly_fields = ("created_at", "updated_at")

    raw_id_fields = ("created_by",)   # ユーザー選択を楽にする
