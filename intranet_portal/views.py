from datetime import timedelta

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils import translation, timezone
from django.conf import settings

HEAT_DAYS = 30


def _heat_name(u):
    if not u:
        return "未割当"
    full = f"{u.last_name} {u.first_name}".strip()
    return full or u.email


def _heat_level(n):
    """入力件数 → 濃さレベル(0..4)。GitHub風。"""
    if n <= 0:
        return 0
    if n <= 2:
        return 1
    if n <= 4:
        return 2
    if n <= 6:
        return 3
    return 4


@login_required
def home(request):
    """
    社内ポータルのトップページ。
    過去30日間の課題報告状況（=進捗の入力件数）を全ユーザー分ヒートマップで表示する。
    Superuser は除外、未入力の人も0件として表示する。
    """
    from django.contrib.auth import get_user_model
    from cs_tasks.models import ProgressUpdate

    User = get_user_model()
    today = timezone.localdate()
    start = today - timedelta(days=HEAT_DAYS - 1)
    days = [start + timedelta(days=i) for i in range(HEAT_DAYS)]

    # 報告 = 進捗の入力（上長コメントはカウントしない）。TZ境界の取りこぼし防止に1日広く取得。
    counts = {}   # user_id -> {date: 件数}
    buf = start - timedelta(days=1)
    for p in ProgressUpdate.objects.filter(created_at__date__gte=buf).only("author", "created_at"):
        if not p.author_id:
            continue
        d = timezone.localtime(p.created_at).date()
        if d < start or d > today:
            continue
        counts.setdefault(p.author_id, {})
        counts[p.author_id][d] = counts[p.author_id].get(d, 0) + 1

    rows = []
    for u in User.objects.filter(is_active=True, is_superuser=False):
        cmap = counts.get(u.id, {})
        cells = [{"date": d, "n": cmap.get(d, 0), "lvl": _heat_level(cmap.get(d, 0))} for d in days]
        rows.append({"name": _heat_name(u), "total": sum(c["n"] for c in cells), "cells": cells})
    rows.sort(key=lambda r: r["name"])

    return render(request, "portal/home.html", {
        "heat_rows": rows,
        "heat_days": days,
        "heat_start": start,
        "heat_end": today,
    })


def switch_language(request, lang_code):
    """
    ポータル共通の言語切替ビュー。
    /lang/ja/, /lang/zh-hans/ から呼ばれる想定。
    ログイン前（ログイン画面）でも切替できるよう login_required は付けない。
    """
    supported = dict(settings.LANGUAGES).keys()
    if lang_code not in supported:
        lang_code = settings.LANGUAGE_CODE  # 不正なコードならデフォルト言語に戻す

    # 言語をアクティブ化 & cookie に保存
    translation.activate(lang_code)
    response = redirect(request.META.get("HTTP_REFERER", "/"))
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang_code)

    return response