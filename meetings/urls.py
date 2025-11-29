# meetings/urls.py
from django.urls import path
from . import views

app_name = "meetings"

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("add/", views.add_meeting, name="add_meeting"),
    path("inline-update/", views.inline_update, name="inline_update"),
    path("toggle-undecided/<int:pk>/", views.toggle_undecided, name="toggle_undecided"),
    path("cancel/<int:pk>/", views.cancel_meeting, name="cancel_meeting"),
]
