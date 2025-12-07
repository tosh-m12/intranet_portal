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
    """
    EnvSettings は「1件だけ」の想定なので:
      - is_fetching_history は読み取り専用
      - log_times を見やすく表示
      - 追加・削除を基本禁止（必要ならここを緩めればOK）
    """
    list_display = (
        "id",
        "interval",
        "cache_interval",
        "cache_expire_hours",
        "log_directory",
        "history_fetch_time",
        "display_log_times",
        "is_fetching_history",
    )

    readonly_fields = ("is_fetching_history",)

    fieldsets = (
        ("フロント表示設定", {
            "fields": ("interval",),
        }),
        ("キャッシュ / ログ設定", {
            "fields": (
                "cache_interval",
                "cache_expire_hours",
                "log_directory",
                "log_times",
            ),
        }),
        ("履歴取得設定", {
            "fields": (
                "history_fetch_time",
                "is_fetching_history",
            ),
        }),
    )

    def display_log_times(self, obj):
        if not obj.log_times:
            return ""
        # ["09:00", "15:00"] → "09:00, 15:00" みたいな表示にする
        return ", ".join(obj.log_times)
    display_log_times.short_description = "ログ取得時刻一覧"

    # EnvSettings を 1件だけに制限（任意）
    def has_add_permission(self, request):
        # まだ1件もなければ追加を許可
        if EnvSettings.objects.count() == 0:
            return True
        return False

    def has_delete_permission(self, request, obj=None):
        # 削除は基本禁止（誤操作防止）
        return False


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
