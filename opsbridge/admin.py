from django.contrib import admin

from .models import OpsAuditLog, OpsProcessedMessage


@admin.register(OpsAuditLog)
class OpsAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "model_label", "target_pk", "action", "actor")
    list_filter = ("model_label", "action")
    search_fields = ("target_pk", "model_label")
    readonly_fields = ("model_label", "target_pk", "action",
                       "before_json", "after_json", "actor", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(OpsProcessedMessage)
class OpsProcessedMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "nonce")
    search_fields = ("nonce",)
    readonly_fields = ("nonce", "raw_body", "created_at")

    def has_add_permission(self, request):
        return False
