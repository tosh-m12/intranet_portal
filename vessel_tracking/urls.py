from django.urls import path

from . import views

app_name = 'vessel_tracking'

urlpatterns = [
    path('', views.shipment_list, name='index'),
    path('list/', views.shipment_list, name='list'),
    path('monitor/', views.monitor, name='monitor'),
    path('quick/', views.quick_create, name='quick_create'),
    path('detail/<int:pk>/', views.detail, name='detail'),
    path('entry/', views.entry, name='entry'),
    path('entry/<int:pk>/', views.entry, name='edit'),
    path('entry/<int:pk>/cancel/', views.cancel, name='cancel'),
    path('customers/', views.customer_list, name='customers'),
    path('customers/add/', views.customer_add, name='customer_add'),
    # 候補サジェスト用 JSON API
    path('api/customers/', views.api_customers, name='api_customers'),
]
