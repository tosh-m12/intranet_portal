from django.urls import path

from . import views

app_name = 'vessel_tracking'

urlpatterns = [
    path('', views.shipment_list, name='index'),
    path('list/', views.shipment_list, name='list'),
    path('monitor/', views.monitor, name='monitor'),
    path('monitor/refresh/', views.monitor_refresh, name='monitor_refresh'),
    path('quick/', views.quick_create, name='quick_create'),
    path('dup-check/', views.dup_check, name='dup_check'),
    path('detail/<int:pk>/', views.detail, name='detail'),
    path('entry/', views.entry, name='entry'),
    path('entry/<int:pk>/', views.entry, name='edit'),
    path('entry/<int:pk>/cancel/', views.cancel, name='cancel'),
]
