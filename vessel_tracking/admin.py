from django.contrib import admin

from .models import Customer, Shipment


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active')
    search_fields = ('code', 'name')
    list_filter = ('is_active',)


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'inv_no', 'customer', 'dest', 'container_type',
                    'etd', 'atd', 'eta', 'ata', 'vessel', 'voyage', 'assignee',
                    'source', 'is_cancelled')
    search_fields = ('order_no', 'inv_no', 'vessel', 'voyage')
    list_filter = ('customer', 'dest', 'container_type', 'source', 'is_cancelled')
    date_hierarchy = 'etd'
