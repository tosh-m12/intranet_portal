from django.urls import path

from . import bridge_api

app_name = "opsbridge"

urlpatterns = [
    # 汎用メンテナンスAPI(Cloudflare Tunnel 経由・Bearer + writeback は HMAC)
    path("api/export", bridge_api.ops_export, name="api_export"),
    path("api/writeback", bridge_api.ops_writeback, name="api_writeback"),
]
