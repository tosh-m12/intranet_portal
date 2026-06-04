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
    担当者別の課題入力状況を過去30日分のヒートマップで表示する。
    """
    from cs_tasks.models import Task, ProgressUpdate, SupervisorComment

    today = timezone.localdate()
    start = today - timedelta(days=HEAT_DAYS - 1)
    days = [start + timedelta(days=i) for i in range(HEAT_DAYS)]

    counts = {}   # user_id -> {date: 件数}
    users = {}    # user_id -> user

    def add(author, created_at):
        if not author or not created_at:
            return
        d = timezone.localtime(created_at).date()
        if d < start or d > today:
            return
        users[author.id] = author
        counts.setdefault(author.id, {})
        counts[author.id][d] = counts[author.id].get(d, 0) + 1

    # 入力 = 進捗 + 上長コメント（区分は分けずトータル）。TZ境界の取りこぼし防止に1日広く取得。
    buf = start - timedelta(days=1)
    for p in ProgressUpdate.objects.filter(created_at__date__gte=buf).select_related("author"):
        add(p.author, p.created_at)
    for c in SupervisorComment.objects.filter(created_at__date__gte=buf).select_related("author"):
        add(c.author, c.created_at)
    # 活動0でも担当者は行として出す（非中止課題の担当者）
    for t in Task.objects.filter(is_cancelled=False).select_related("assignee"):
        if t.assignee:
            users.setdefault(t.assignee.id, t.assignee)

    rows = []
    for uid, u in users.items():
        cmap = counts.get(uid, {})
        cells = [{"date": d, "n": cmap.get(d, 0), "lvl": _heat_level(cmap.get(d, 0))} for d in days]
        rows.append({"name": _heat_name(u), "total": sum(c["n"] for c in cells), "cells": cells})
    rows.sort(key=lambda r: r["name"])

    return render(request, "portal/home.html", {
        "heat_rows": rows,
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