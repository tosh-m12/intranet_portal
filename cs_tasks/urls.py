# cs_tasks/urls.py
from django.urls import path
from . import views
from .bridge import api as bridge_api

app_name = "cs_tasks"

urlpatterns = [
    # リアルタイム連携API(Mac cs_bridge がトンネル越しに叩く。メール往復の置換)
    path("bridge/api/sync", bridge_api.bridge_sync, name="bridge_api_sync"),
    path("bridge/api/writeback", bridge_api.bridge_writeback, name="bridge_api_writeback"),
    path("", views.index, name="index"),
    path("my/", views.my_tasks, name="my"),
    path("new/", views.task_new, name="new"),
    path("add/", views.task_add_inline, name="task_add_inline"),
    path("<int:task_id>/edit/", views.task_edit, name="edit"),
    path("<int:task_id>/title/", views.edit_title, name="edit_title"),
    path("<int:task_id>/description/", views.edit_description, name="edit_description"),
    path("client/", views.edit_client, name="edit_client"),
    path("<int:task_id>/progress/", views.add_progress, name="add_progress"),
    path("progress/<int:progress_id>/date/", views.edit_progress_date, name="edit_progress_date"),
    path("<int:task_id>/complete/", views.toggle_complete, name="toggle_complete"),
    path("<int:task_id>/cancel/", views.toggle_cancel, name="toggle_cancel"),
    path("progress/<int:progress_id>/edit/", views.edit_progress, name="edit_progress"),
    path("progress/<int:progress_id>/comment/", views.add_comment, name="add_comment"),
    path("comment/<int:comment_id>/edit/", views.edit_comment, name="edit_comment"),
    path(
        "progress/<int:progress_id>/close/",
        views.toggle_progress_close,
        name="toggle_progress_close",
    ),
    path("mailing-list/", views.mailing_list, name="mailing_list"),
    path("weekly-report/", views.weekly_report, name="weekly_report"),
]
