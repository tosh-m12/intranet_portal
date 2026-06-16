from django.urls import path

from . import views

app_name = 'billing'

urlpatterns = [
    path('', views.invoice_list, name='index'),
    path('list/', views.invoice_list, name='list'),
    path('detail/<int:pk>/', views.detail, name='detail'),
    path('detail/<int:pk>/approve/', views.approve, name='approve'),
    path('entry/', views.entry, name='entry'),
    path('entry/<int:pk>/', views.entry, name='edit'),
    path('entry/<int:pk>/cancel/', views.cancel, name='cancel'),
    path('master/', views.master_list, name='master'),
    path('master/add/', views.master_add, name='master_add'),
    path('master/<int:pk>/summary/', views.master_summary, name='master_summary'),
    # 候補サジェスト/検証用 JSON API
    path('api/parties/', views.api_parties, name='api_parties'),
    path('api/check-company/', views.api_check_company, name='api_check_company'),
]
