# envmon/urls.py
from django.urls import path

from . import views

app_name = "envmon"

urlpatterns = [
    path("", views.index, name="index"),                      # 倉庫別トップ
    path("all-devices/", views.all_devices, name="all_devices"),
    path("data/", views.data_api, name="data_api"),           # JS 用 API
    path("settings/", views.settings_view, name="settings"),
    path("locations/", views.edit_locations, name="locations"),
    path("warehouse-assign/", views.warehouse_assign, name="warehouse_assign"),
    path("save-assignment/", views.save_assignment, name="save_assignment"),
    path("history/fetch-all/", views.fetch_history_all, name="fetch_history_all"),
    path("history/download/", views.download_history_csv, name="download_history"),
    path(
        "download_history/warehouse/",
        views.download_history_by_warehouse,
        name="download_history_by_warehouse",
    ),
    path(
        "download_history/all_range/",
        views.download_history_all_range,
        name="download_history_all_range",
    ),

    path("data/", views.data_api, name="data_api"),
]
