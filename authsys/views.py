from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test, login_required
from django.contrib import messages
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import logout
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin

from .forms import ResetPasswordForm
import secrets
import string, logging

logger = logging.getLogger(__name__)

User = get_user_model()

def is_admin(user):
    return user.is_superuser or user.is_staff


def is_staff_user(user):
    return user.is_authenticated and user.is_staff


@user_passes_test(is_admin)
def user_management(request):

    if request.method == "POST":
        action = request.POST.get("action")
        is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

        # ① 既存ユーザー更新
        if action == "update":
            user_id = request.POST.get("user_id")
            target = get_object_or_404(User, pk=user_id)

            target.last_name = request.POST.get("last_name", "").strip()
            target.first_name = request.POST.get("first_name", "").strip()
            target.email = request.POST.get("email", "").strip().lower()

            # 権限（管理者/一般ユーザー）の付与・解除。
            # ・スーパーユーザーの権限は変更しない（is_staff を維持）
            role = request.POST.get("role")
            if role in ("admin", "user") and not target.is_superuser:
                target.is_staff = (role == "admin")

            target.save()

            if is_ajax:
                return JsonResponse({"status": "ok", "id": target.id, "is_staff": target.is_staff})
            messages.success(request, f"{target.last_name} {target.first_name} さんを更新しました。")
            return redirect("authsys:user_management")

        # ② 削除（管理者と自分は削除不可）
        elif action == "delete":
            user_id = request.POST.get("user_id")
            target = get_object_or_404(User, pk=user_id)

            if target.is_superuser or target == request.user:
                msg = "管理者または自身のアカウントは削除できません。"
                if is_ajax:
                    return JsonResponse({"status": "error", "message": msg}, status=400)
                messages.error(request, msg)
            else:
                target.delete()
                if is_ajax:
                    return JsonResponse({"status": "ok"})
                messages.success(request, "ユーザーを削除しました。")

            return redirect("authsys:user_management")

        # ③ 新規ユーザー作成
        elif action == "create":
            last_name = request.POST.get("last_name", "").strip()
            first_name = request.POST.get("first_name", "").strip()
            email = request.POST.get("email", "").strip().lower()

            if not email:
                messages.error(request, "メールアドレスは必須です。")
                return redirect("authsys:user_management")

            # User の USERNAME_FIELD は email なので username フィールドは使わない
            chars = string.ascii_letters + string.digits
            raw_password = "".join(secrets.choice(chars) for _ in range(12))

            user = User.objects.create_user(
                email=email,
                first_name=first_name,
                last_name=last_name,
                password=raw_password,
            )

            # 権限（管理者として作成するか）
            role = request.POST.get("role", "user")
            user.is_staff = (role == "admin")
            user.must_change_password = True
            # 新規ユーザーは一覧の末尾に配置
            from django.db.models import Max
            max_order = User.objects.filter(is_superuser=False).aggregate(
                m=Max("display_order")
            )["m"] or 0
            user.display_order = max_order + 1
            user.save(update_fields=["must_change_password", "is_staff", "display_order"])

            subject = "【社内ポータル】アカウントが作成されました"
            message = (
                f"{last_name} {first_name} 様\n\n"
                "社内ポータルのアカウントが作成されました。\n"
                f"ログインID（メールアドレス）: {email}\n"
                f"初回パスワード: {raw_password}\n\n"
                "初回ログイン後はパスワードの変更をお願いします。\n"
            )
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
            send_mail(subject, message, from_email, [email], fail_silently=False)

            if is_ajax:
                return JsonResponse({"status": "ok", "id": user.id})

            messages.success(request, f"{last_name} {first_name} さんを新規登録しました。")
            return redirect("authsys:user_management")

        # ④ 並び替え（ドラッグ&ドロップ）。order[] にユーザーIDを表示順で受け取る
        elif action == "reorder":
            ids = request.POST.getlist("order[]") or request.POST.getlist("order")
            # 受け取った順に display_order を 1 から振り直す（superuser は対象外）
            valid_ids = set(
                User.objects.filter(is_superuser=False).values_list("id", flat=True)
            )
            for index, uid in enumerate(ids, start=1):
                try:
                    pk = int(uid)
                except (TypeError, ValueError):
                    continue
                if pk in valid_ids:
                    User.objects.filter(pk=pk).update(display_order=index)

            if is_ajax:
                return JsonResponse({"status": "ok"})
            return redirect("authsys:user_management")

    # GET / 再表示
    # superuser はこの画面の管理対象外（Django admin 等で別管理）。
    users = User.objects.filter(is_superuser=False).order_by(
        "display_order", "last_name", "first_name"
    )
    return render(request, "authsys/user_management.html", {"users": users})


@user_passes_test(is_admin)
@require_POST
def reset_user_password(request, user_id):
    # ① ボタンを押した人（管理者）
    actor = request.user  # 誰が押したか

    # ② ボタンが属していたユーザー（URL の <int:user_id>）
    target = get_object_or_404(User, pk=user_id)

    # ③ 新しいパスワード発行
    chars = string.ascii_letters + string.digits
    new_password = "".join(secrets.choice(chars) for _ in range(12))
    target.set_password(new_password)
    target.must_change_password = True
    target.save(update_fields=["password", "must_change_password"])

    # ④ 実際の送信先
    recipient_list = [target.email]

    # ★ ログ：誰が / 誰のボタンを押して / どこに送ったか
    logger.warning(
        "[AUTH RESET PW] actor(id=%s, email=%s) clicked_button_of(id=%s, email=%s) -> mail_to=%s",
        getattr(actor, "id", None),
        getattr(actor, "email", None),
        target.id,
        target.email,
        recipient_list,
    )

    subject = "【社内ポータル】パスワード再発行のお知らせ"
    message = (
        f"{target.last_name} {target.first_name} 様\n\n"
        "パスワードを再発行しました。\n\n"
        f"ログインID（メールアドレス）: {target.email}\n"
        f"新しいパスワード: {new_password}\n\n"
        "ログイン後はパスワードの変更をお願いします。\n"
    )
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    send_mail(subject, message, from_email, recipient_list, fail_silently=False)

    messages.success(request, f"{target.last_name} {target.first_name} さんのパスワードを再発行しました。")
    return redirect("authsys:user_management")


@login_required
def profile(request):
    return render(request, 'authsys/profile.html')

def logout_view(request):
    logout(request)
    return redirect('authsys:login')

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
            
            user.must_change_password = True
            
            user.save(update_fields=["password", "must_change_password"])

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
            return redirect("authsys:login")  # ログイン画面などへ
    else:
        form = ResetPasswordForm()

    return render(request, "authsys/reset_password.html", {"form": form})

class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    template_name = "authsys/password_change.html"
    success_url = reverse_lazy("authsys:password_change_done")

    def form_valid(self, form):
        response = super().form_valid(form)
        # ★ パスワード変更に成功したらフラグを落とす
        user = self.request.user
        if hasattr(user, "must_change_password"):
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
        return response
    