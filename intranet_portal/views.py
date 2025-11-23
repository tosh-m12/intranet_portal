# intranet_portal/views.py

from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def home(request):
    """
    社内ポータルのトップページ。
    ログイン必須にして、各業務アプリへの入口をまとめる。
    """
    return render(request, "portal/home.html")
