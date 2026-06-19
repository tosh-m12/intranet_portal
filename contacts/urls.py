from django.urls import path

from . import views

app_name = "contacts"

urlpatterns = [
    path("", views.index, name="index"),
    path("inline-update/", views.inline_update, name="inline_update"),
]
