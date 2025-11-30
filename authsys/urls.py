from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth import views as auth_views
from .forms import EmailAuthenticationForm
from . import views
from .forms import EmailAuthenticationForm

app_name = "authsys"

urlpatterns = [
    path('login/', LoginView.as_view(template_name='authsys/login.html', authentication_form=EmailAuthenticationForm), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path("users/", views.user_management, name="user_management"),
    path("reset-password/", views.reset_password, name="reset_password"),
    path(
        "users/<int:user_id>/reset-password/",
        views.reset_user_password,
        name="reset_user_password",
    ),
    path(
        "password/change/",
        views.CustomPasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "password/change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="authsys/password_change_done.html"
        ),
        name="password_change_done",
    ),
]