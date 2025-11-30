# authsys/middleware.py
from django.shortcuts import redirect
from django.urls import reverse
import logging

logger = logging.getLogger(__name__)

class ForcePasswordChangeMiddleware:
    """
    must_change_password=True のユーザーを、
    パスワード変更ページに強制的に飛ばすミドルウェア（シンプル版）。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path

        # まずはログを出して様子を見る
        if user is not None and user.is_authenticated:
            flag = getattr(user, "must_change_password", False)
            logger.warning(
                "[PW_FORCE] user=%s must_change=%s path=%s",
                getattr(user, "email", None),
                flag,
                path,
            )

            if flag:
                password_change_path = reverse("authsys:password_change")
                password_change_done_path = reverse("authsys:password_change_done")
                login_path = reverse("authsys:login")
                logout_path = reverse("authsys:logout")

                # ★ これらのURLだけは通過を許可
                allowed_paths = {
                    password_change_path,
                    password_change_done_path,
                    login_path,
                    logout_path,
                }

                # 上記以外なら必ずパスワード変更ページへ
                if path not in allowed_paths:
                    logger.warning("[PW_FORCE] redirecting to password_change from %s", path)
                    return redirect("authsys:password_change")

        # 未ログイン or フラグ False の場合は普通に通す
        response = self.get_response(request)
        return response
