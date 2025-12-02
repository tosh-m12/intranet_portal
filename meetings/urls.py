# meetings/urls.py
from django.urls import path
from . import views

app_name = "meetings"

urlpatterns = [
    # === 既存 ===
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("add/", views.add_meeting, name="add_meeting"),
    path("inline-update/", views.inline_update, name="inline_update"),
    path("toggle-undecided/<int:pk>/", views.toggle_undecided, name="toggle_undecided"),
    path("cancel/<int:pk>/", views.cancel_meeting, name="cancel_meeting"),

    # === ▼ ここから新規（visitors と同様の構成）===

    # 各種設定画面
    path("settings/", views.settings_view, name="settings"),

    # CSV ダウンロード（meetings 一覧）
    path(
        "settings/download/<str:target>/",
        views.download_settings_csv,
        name="download_settings_csv",
    ),

    # CSV アップロード（meetings 全件入れ替え）
    path(
        "settings/upload/",
        views.upload_meeting_csv,
        name="upload_meeting_csv",
    ),

    # メールプレビュー（GET）
    path(
        "settings/preview-email/",
        views.preview_email,
        name="preview_email",
    ),

    # 今すぐ送信（POST）
    path(
        "settings/run-email/",
        views.run_email,
        name="run_email",
    ),
]
