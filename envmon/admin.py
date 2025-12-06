# envmon/admin.py
from django.contrib import admin
from .models import (
    Location,
    DeviceAssignment,
    EnvSettings,
    AssignmentHistory,
    DeviceHistory,
)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_external", "created_at", "updated_at")
    search_fields = ("code", "name")
    list_filter = ("is_external",)


@admin.register(DeviceAssignment)
class DeviceAssignmentAdmin(admin.ModelAdmin):
    list_display = ("device_id", "location", "updated_at")
    search_fields = ("device_id",)
    list_filter = ("location",)


@admin.register(EnvSettings)
class EnvSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "interval",
        "cache_interval",
        "cache_expire_hours",
        "log_directory",
        "history_fetch_time",
        "is_fetching_history",   # ★ ここで一覧に表示
    )
    readonly_fields = ("is_fetching_history",)  # ★ 変更画面では読み取り専用にする


@admin.register(AssignmentHistory)
class AssignmentHistoryAdmin(admin.ModelAdmin):
    list_display = ("device_id", "location", "changed_at")
    search_fields = ("device_id",)
    list_filter = ("location", "changed_at")


@admin.register(DeviceHistory)
class DeviceHistoryAdmin(admin.ModelAdmin):
    list_display = ("sn", "recorded_at", "temperature", "humidity")
    search_fields = ("sn",)
    list_filter = ("sn", "recorded_at")
    ordering = ("-recorded_at",)
