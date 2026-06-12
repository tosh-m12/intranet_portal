from django.contrib import admin

from .models import Shipment


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('order_no', 'inv_no', 'customer', 'dest', 'container_type',
                    'etd', 'atd', 'eta', 'ata', 'vessel', 'voyage', 'assignee',
                    'source', 'is_cancelled')
    search_fields = ('order_no', 'inv_no', 'customer', 'vessel', 'voyage')
    list_filter = ('dest', 'container_type', 'source', 'is_cancelled')
    date_hierarchy = 'etd'
