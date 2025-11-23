from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils import translation
from django.conf import settings

@login_required
def home(request):
    """
    社内ポータルのトップページ。
    ログイン必須にして、各業務アプリへの入口をまとめる。
    """
    return render(request, "portal/home.html")


@login_required
def switch_language(request, lang_code):
    """
    ポータル共通の言語切替ビュー。
    /lang/ja/, /lang/zh-hans/ から呼ばれる想定。
    """
    supported = dict(settings.LANGUAGES).keys()
    if lang_code not in supported:
        lang_code = settings.LANGUAGE_CODE  # 不正なコードならデフォルト言語に戻す

    # 言語をアクティブ化 & cookie に保存
    translation.activate(lang_code)
    response = redirect(request.META.get("HTTP_REFERER", "/"))
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang_code)

    return response