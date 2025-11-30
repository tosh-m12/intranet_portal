# mailcenter/admin.py
from django.contrib import admin
from .models import MailAccount


@admin.register(MailAccount)
class MailAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "smtp_host", "smtp_user")
    search_fields = ("code", "name", "smtp_user")
