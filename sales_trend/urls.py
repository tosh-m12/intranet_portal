from django.urls import path

from . import views

app_name = 'sales_trend'

urlpatterns = [
    path('', views.index, name='index'),
    path('api/data/', views.data_api, name='data_api'),
    path('partner/', views.partner_overview, name='partner'),
]
