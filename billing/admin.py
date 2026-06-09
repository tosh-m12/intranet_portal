from django.contrib import admin

from .models import InvoiceLine, MasterParty


@admin.register(MasterParty)
class MasterPartyAdmin(admin.ModelAdmin):
    list_display = ('group_name', 'company_name', 'assignee')
    search_fields = ('group_name', 'company_name', 'assignee')
    list_filter = ('assignee',)


@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    list_display = ('serial', 'customer_gc', 'bill_to', 'bill_year', 'bill_month',
                    'total_after_tax', 'assignee', 'source', 'is_cancelled')
    search_fields = ('serial', 'customer_gc', 'bill_to')
    list_filter = ('assignee', 'bill_year', 'bill_month', 'source', 'is_cancelled')
