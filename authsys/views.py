from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth.decorators import user_passes_test, login_required
from django.contrib import messages
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import logout

from .forms import InviteUserForm, ResetPasswordForm


def is_staff_user(user):
    return user.is_authenticated and user.is_staff

# ========== ① 新規メンバー招待 ==========
@user_passes_test(is_staff_user)
def invite_user(request):
    if request.method == "POST":
        form = InviteUserForm(request.POST)
        if form.is_valid():
            full_name = form.cleaned_data["full_name"].strip()
            email = form.cleaned_data["email"].strip().lower()

            # username はメールアドレスで統一
            username = email

            # 10文字のランダムパスワードを生成
            temp_password = get_random_string(10)

            # 既に存在する場合は上書き（社内向けなのでシンプルに）
            user, created = User.objects.get_or_create(username=username, defaults={
                "email": email,
            })

            # 氏名を name に入れる（お好みで first_name/last_name に分割してもOK）
            user.email = email
            user.first_name = full_name
            user.is_active = True
            user.set_password(temp_password)
            user.save()

            # メール本文
            login_url = request.build_absolute_uri("/")  # ログイン画面URLに変えてもOK
            subject = "【NGLS-CS Portal】アカウント情報のご案内"
            message = (
                f"{full_name} 様\n\n"
                "NGLS-CS Portal へのログイン情報をお送りします。\n\n"
                f"ログインURL: {login_url}\n"
                f"ログインID（メールアドレス）: {email}\n"
                f"初期パスワード: {temp_password}\n\n"
                "初回ログイン後、パスワードの変更をお願いします。\n"
            )

            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )

            messages.success(request, f"{email} に招待メールを送信しました。")
            return redirect("authsys:invite_user")
    else:
        form = InviteUserForm()

    return render(request, "authsys/invite_user.html", {"form": form})


@login_required
def profile(request):
    return render(request, 'authsys/profile.html')

def logout_view(request):
    logout(request)
    return redirect('login')

# ========== ② パスワード再発行 ==========
def reset_password(request):
    if request.method == "POST":
        form = ResetPasswordForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()

            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                # セキュリティ的には「存在しない」とは教えず成功風に返す
                messages.info(request, "パスワード再設定用のメールを送信しました。")
                return redirect("authsys:reset_password")

            # 新しいランダムパスワードを発行
            new_password = get_random_string(10)
            user.set_password(new_password)
            user.save()

            subject = "【NGLS-CS Portal】パスワード再発行のお知らせ"
            message = (
                f"{user.first_name or ''} 様\n\n"
                "パスワードを再発行しました。\n\n"
                f"ログインID（メールアドレス）: {email}\n"
                f"新しいパスワード: {new_password}\n\n"
                "ログイン後、お早めにパスワードの変更をお願いします。\n"
            )

            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )

            messages.success(request, "パスワード再発行メールを送信しました。")
            return redirect("login")  # ログイン画面などへ
    else:
        form = ResetPasswordForm()

    return render(request, "authsys/reset_password.html", {"form": form})