# cs_tasks/admin.py
from django.contrib import admin
from django.urls import path
from django.shortcuts import redirect
from django.contrib import messages
from django.template.response import TemplateResponse

from .models import (
    Task,
    ProgressUpdate,
    SupervisorComment,
    WeeklyReportMailingList,
    WeeklyReportConfig,
)


class ProgressUpdateInline(admin.TabularInline):
    model = ProgressUpdate
    extra = 0
    raw_id_fields = ("author", "closed_by")


class SupervisorCommentInline(admin.StackedInline):
    model = SupervisorComment
    extra = 0
    raw_id_fields = ("author",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "client_name",
        "owner",
        "assignee",
        "due_date",
        "is_closed",
        "is_hidden",
        "created_at",
    )
    list_filter = ("is_closed", "is_hidden", "assignee")
    search_fields = ("title", "client_name", "description")
    raw_id_fields = ("owner", "assignee", "completed_by")
    readonly_fields = ("created_at", "updated_at", "completed_at", "completed_by")
    inlines = [ProgressUpdateInline]


@admin.register(ProgressUpdate)
class ProgressUpdateAdmin(admin.ModelAdmin):
    list_display = ("task", "content", "is_closed", "created_at")
    list_filter = ("is_closed",)
    raw_id_fields = ("task", "author", "closed_by")
    inlines = [SupervisorCommentInline]


@admin.register(WeeklyReportMailingList)
class WeeklyReportMailingListAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "email")


@admin.register(WeeklyReportConfig)
class WeeklyReportConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "send_weekday", "send_time", "mode", "last_sent_date")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "send-now/",
                self.admin_site.admin_view(self.send_now_view),
                name="cs_tasks_weeklyreportconfig_send_now",
            ),
        ]
        return custom + urls

    def send_now_view(self, request):
        """「今すぐ週報を送信」ボタンの処理。"""
        from .email_utils import send_weekly_report

        if request.method == "POST":
            result = send_weekly_report(ignore_schedule=True)
            if result.get("sent"):
                recipients = result.get("recipients") or []
                self.message_user(
                    request,
                    f"週報を送信しました。宛先: {', '.join(recipients)}",
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    f"送信に失敗しました。{result.get('reason', '')}",
                    level=messages.ERROR,
                )
            return redirect("admin:cs_tasks_weeklyreportconfig_changelist")

        context = {
            **self.admin_site.each_context(request),
            "title": "週報を今すぐ送信",
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request, "cs_tasks/admin_send_now.html", context
        )

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["show_send_now_button"] = True
        return super().changelist_view(request, extra_context=extra_context)
