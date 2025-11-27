from django.urls import path
from . import views

app_name = 'visitors'

urlpatterns = [
    path('', views.index, name='index'),
    path('add/', views.add_visitor, name='add_visitor'),
    path('cancel/<int:id>/', views.cancel_visitor, name='cancel_visitor'),
    path('edit/<int:id>/', views.edit_visitor, name='edit_visitor'),
    path('settings/', views.settings_view, name='settings'),
    path('run-email/', views.run_email, name='run_email'),
    path('inline-update/', views.inline_update, name='inline_update'),
    path('<int:id>/toggle-undecided/', views.toggle_undecided, name='toggle_undecided'),
]
