from django.urls import path
from . import views

app_name = 'visitors'

urlpatterns = [
    path('', views.index, name='index'),
    path('add/', views.add_visitor, name='add_visitor'),
    path('cancel/<int:id>/', views.cancel_visitor, name='cancel_visitor'),
    path('settings/', views.settings_view, name='settings'),
    path("history/", views.history, name="history"),
    path('run-email/', views.run_email, name='run_email'),
    path('inline-update/', views.inline_update, name='inline_update'),
    path('<int:id>/toggle-undecided/', views.toggle_undecided, name='toggle_undecided'),
    path('settings/download/<str:kind>/', views.download_settings_csv, name='download_settings_csv'),
    path('settings/upload/visitor_list/', views.upload_visitor_csv, name='upload_visitor_csv')
]
