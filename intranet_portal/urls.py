from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('admin/', admin.site.urls),

    # ★ namespace は include に渡す
    path(
        'accounts/',
        include('authsys.urls', namespace='authsys'),   # 認証アプリ
    ),

    path('visitors/', include('visitors.urls')),   # 来客予定アプリ
    path('meetings/', include('meetings.urls')),
    path('lang/<str:lang_code>/', views.switch_language, name='switch_language'),
    path('', views.home, name='home'),             # ポータルトップ

    # ★ ログアウト後は authsys:login へ
    path(
        'logout/',
        auth_views.LogoutView.as_view(next_page='authsys:login'),
        name='logout',
    ),
    path("envmon/", include("envmon.urls")),
    path("cs-tasks/", include("cs_tasks.urls")),
    path("billing/", include("billing.urls")),
    path("sales-trend/", include("sales_trend.urls")),
    path("vessel/", include("vessel_tracking.urls")),
    path("ops/", include("opsbridge.urls")),   # 汎用メンテナンスAPI(opsbridge)
    path("contacts/", include("contacts.urls")),   # 相手先名簿(来客+訪問 横断)
]
