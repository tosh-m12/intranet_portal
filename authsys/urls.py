from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from .forms import EmailAuthenticationForm
from . import views

app_name = "authsys"

urlpatterns = [
    path('login/', LoginView.as_view(template_name='authsys/login.html', authentication_form=EmailAuthenticationForm), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path("invite/", views.invite_user, name="invite_user"),
    path("reset-password/", views.reset_password, name="reset_password"),
]