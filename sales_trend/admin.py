from django.contrib import admin

from .models import Customer, MonthlySale


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('code', 'group_name', 'customer_name', 'klass', 'other_start_year')
    list_filter = ('klass', 'other_start_year')
    search_fields = ('code', 'group_name', 'customer_name')


@admin.register(MonthlySale)
class MonthlySaleAdmin(admin.ModelAdmin):
    list_display = ('customer', 'year', 'month', 'amount')
    list_filter = ('year', 'month')
    search_fields = ('customer__code', 'customer__group_name')
