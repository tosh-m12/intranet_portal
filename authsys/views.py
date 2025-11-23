from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib.auth import logout


@login_required
def profile(request):
    return render(request, 'authsys/profile.html')

def logout_view(request):
    logout(request)
    return redirect('login')