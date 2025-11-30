# authsys/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # ★ username ではなく email でソート
    ordering = ("email",)

    # 一覧に出したい項目
    list_display = ("email", "last_name", "first_name", "is_staff", "must_change_password")

    # 検索対象
    search_fields = ("email", "last_name", "first_name")

    # 編集画面のレイアウト
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
        ("パスワード強制変更", {"fields": ("must_change_password",)}),
    )

    # 追加画面のレイアウト（createsuperuser などで使う）
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "email",
                "password1",
                "password2",
                "is_staff",
                "is_superuser",
                "must_change_password",
            ),
        }),
    )