from django.urls import path
from . import views

app_name = "envmon"

urlpatterns = [
    path("", views.index, name="index"),
    path("all-devices/", views.all_devices, name="all_devices"),
    path("data/", views.data_api, name="data_api"),
    path("settings/", views.settings_view, name="settings"),
    path("locations/", views.edit_locations, name="locations"),
    path("warehouse-assign/", views.warehouse_assign, name="warehouse_assign"),
    path("save-assignment/", views.save_assignment, name="save_assignment"),

    # 履歴関連
    path("history_7days/", views.history_7days, name="history_7days"),

    # ★ CSVダウンロード専用ページ（一般ユーザー可）
    path("history/csv/", views.history_csv_menu, name="history_csv_menu"),

    # CSVダウンロード実処理（POST）
    path("history/fetch-all/", views.fetch_history_all, name="fetch_history_all"),
    path("history/download/", views.download_history_csv, name="download_history"),
    path("download_history/warehouse/", views.download_history_by_warehouse,
         name="download_history_by_warehouse"),
    path("download_history/all_range/", views.download_history_all_range,
         name="download_history_all_range"),

    # 履歴手動取得
    path("history/manual-fetch/", views.manual_fetch_history, name="manual_fetch_history"),
]
