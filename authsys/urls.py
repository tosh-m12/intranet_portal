from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from .forms import EmailAuthenticationForm
from . import views

urlpatterns = [
    path('login/', LoginView.as_view(template_name='authsys/login.html', authentication_form=EmailAuthenticationForm), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
]