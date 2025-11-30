# working_schedule/admin.py
from django.contrib import admin
from .models import HolidayDate, SpecialWorkingDay


@admin.register(HolidayDate)
class HolidayDateAdmin(admin.ModelAdmin):
    list_display = ("date", "name")
    list_filter = ("date",)
    search_fields = ("name",)


@admin.register(SpecialWorkingDay)
class SpecialWorkingDayAdmin(admin.ModelAdmin):
    list_display = ("date", "name")
    list_filter = ("date",)
    search_fields = ("name",)
