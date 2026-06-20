# cs_tasks/email_utils.py
from datetime import date, timedelta
import logging

from django.template.loader import render_to_string
from django.utils import translation
from django.utils.timezone import localdate

from .models import Task, ProgressUpdate, WeeklyReportMailingList
from mailcenter.email_utils import send_html_mail

logger = logging.getLogger(__name__)

# レポートメールの言語コード(日本語版・中文版を別便で送信)
LANG_JA = "ja"
LANG_ZH = "zh-hans"
DEFAULT_SUBJECT = {LANG_JA: "CS課題 週次レポート", LANG_ZH: "CS课题 周报"}

# 期限「間近」とみなす日数
DUE_SOON_DAYS = 3

# 共通メールアカウント。専用 'cs_report' が無ければ send_html_mail 側で
# 既存の共通アカウントにフォールバックする。
MAIL_ACCOUNT_CODE = "cs_report"


def get_recipients():
    """有効な週報宛先のメールアドレス一覧。"""
    return list(
        WeeklyReportMailingList.objects.filter(is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )


def build_weekly_report_context(today: date):
    """
    週次レポート用のセクション別データを組み立てる。
    当週 = today を含む週（月曜〜日曜）。
    """
    week_start = today - timedelta(days=today.weekday())  # 月曜
    week_end = week_start + timedelta(days=6)             # 日曜

    active = Task.objects.filter(is_hidden=False).select_related(
        "owner", "assignee"
    )

    # ① 当週新規
    new_tasks = active.filter(created_at__date__range=(week_start, week_end))

    # ② 当週進捗あり
    progressed_ids = (
        ProgressUpdate.objects.filter(
            created_at__date__range=(week_start, week_end)
        )
        .values_list("task_id", flat=True)
        .distinct()
    )
    progressed_tasks = active.filter(id__in=list(progressed_ids))

    # ③ 当週完了
    completed_tasks = Task.objects.filter(
        is_closed=True,
        completed_at__date__range=(week_start, week_end),
    ).select_related("owner", "assignee")

    # ④ 期限超過・間近（進行中のみ）
    open_qs = active.filter(is_closed=False, due_date__isnull=False)
    overdue_tasks = open_qs.filter(due_date__lt=today)
    due_soon_tasks = open_qs.filter(
        due_date__gte=today,
        due_date__lte=today + timedelta(days=DUE_SOON_DAYS),
    )

    # ⑤ サマリー
    summary = {
        "in_progress": Task.objects.filter(
            is_closed=False, is_hidden=False
        ).count(),
        "closed": Task.objects.filter(is_closed=True).count(),
        "hidden": Task.objects.filter(is_hidden=True).count(),
    }

    return {
        "today": today,
        "week_start": week_start,
        "week_end": week_end,
        "new_tasks": new_tasks,
        "progressed_tasks": progressed_tasks,
        "completed_tasks": completed_tasks,
        "overdue_tasks": overdue_tasks,
        "due_soon_tasks": due_soon_tasks,
        "summary": summary,
    }


def compose_weekly_email(lang=LANG_JA, today: date = None, body=None, subject=None):
    """指定言語(lang='ja'/'zh-hans')の週報メール (件名, HTML本文) を組み立てる。
    本文(body)の下にレポート画面と同じ課題表(区分別)を、その言語で配置する。
    body/subject を渡せば未保存プレビュー値で組める(省略時は config の保存値)。"""
    from .models import WeeklyReportConfig
    from .views import build_report_sections   # 遅延 import で循環参照回避

    today = today or localdate()
    config, _ = WeeklyReportConfig.objects.get_or_create(pk=1)
    if lang == LANG_ZH:
        subject = (config.subject_zh if subject is None else subject)
        body = (config.body_zh if body is None else body)
    else:
        subject = (config.subject if subject is None else subject)
        body = (config.body if body is None else body)
    subject = (subject or "").strip() or DEFAULT_SUBJECT[lang]
    # 表の表示言語を override(display_* が当該言語を返す)してレンダ
    with translation.override(lang):
        sections = build_report_sections(user=None)
        html = render_to_string(
            "cs_tasks/email_weekly.html",
            {"body": body, "sections": sections, "today": today},
        )
    return subject, html


def send_weekly_report(ignore_schedule: bool = False):
    """
    週次レポートをメーリングリスト宛に「日本語版」「中文版」の2通HTMLメールで送信する。
    件名・本文は詳細設定(WeeklyReportConfig)の値を使い、本文の下にレポート表を付ける。
    中文版は件名・本文どちらも未翻訳(空)の場合は送らない(=Mac翻訳前は日本語のみ)。

    ignore_schedule:
        True  → スケジュール条件を無視して送信（手動送信・管理画面ボタン用）
        False → スケジューラから呼ばれる想定（呼び出し側で曜日/時刻判定済み）
    """
    from .models import WeeklyReportConfig

    today = localdate()
    logger.info(
        "[CSTASKS_MAIL] send_weekly_report start: today=%s, ignore_schedule=%s",
        today, ignore_schedule,
    )

    result = {"sent": False, "reason": "", "recipients": [], "sent_langs": []}

    recipients = get_recipients()
    result["recipients"] = recipients
    if not recipients:
        msg = "メーリングリストが空のため送信しません。"
        logger.warning("[CSTASKS_MAIL] %s", msg)
        result["reason"] = msg
        return result

    config, _ = WeeklyReportConfig.objects.get_or_create(pk=1)
    langs = [LANG_JA]
    # 中文版は翻訳済み(件名 or 本文あり)の時だけ送る
    if (config.subject_zh or "").strip() or (config.body_zh or "").strip():
        langs.append(LANG_ZH)

    errors = []
    for lang in langs:
        subject, html_content = compose_weekly_email(lang, today)
        send_res = send_html_mail(
            subject=subject,
            html_body=html_content,
            recipients=recipients,
            account_code=MAIL_ACCOUNT_CODE,
        )
        if send_res.get("sent"):
            result["sent_langs"].append(lang)
            logger.info("[CSTASKS_MAIL] %s版 送信完了: %s 件", lang, len(recipients))
        else:
            errors.append(f"{lang}: {send_res.get('reason', 'unknown error')}")
            logger.warning("[CSTASKS_MAIL] %s版 送信失敗: %s", lang, errors[-1])

    result["sent"] = bool(result["sent_langs"])
    if errors:
        result["reason"] = "; ".join(errors)
    return result
