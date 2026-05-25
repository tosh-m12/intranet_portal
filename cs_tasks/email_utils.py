# cs_tasks/email_utils.py
from datetime import date, timedelta
import logging

from django.template.loader import render_to_string
from django.utils.timezone import localdate

from .models import Task, ProgressUpdate, WeeklyReportMailingList
from mailcenter.email_utils import send_html_mail

logger = logging.getLogger(__name__)

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

    active = Task.objects.filter(is_cancelled=False).select_related(
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
            is_closed=False, is_cancelled=False
        ).count(),
        "closed": Task.objects.filter(is_closed=True).count(),
        "cancelled": Task.objects.filter(is_cancelled=True).count(),
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


def send_weekly_report(ignore_schedule: bool = False):
    """
    週次レポートをメーリングリスト宛にHTMLメールで送信する。

    ignore_schedule:
        True  → スケジュール条件を無視して送信（手動送信・管理画面ボタン用）
        False → スケジューラから呼ばれる想定（呼び出し側で曜日/時刻判定済み）
    """
    today = localdate()
    logger.info(
        "[CSTASKS_MAIL] send_weekly_report start: today=%s, ignore_schedule=%s",
        today, ignore_schedule,
    )

    result = {"sent": False, "reason": "", "recipients": []}

    recipients = get_recipients()
    result["recipients"] = recipients
    if not recipients:
        msg = "メーリングリストが空のため送信しません。"
        logger.warning("[CSTASKS_MAIL] %s", msg)
        result["reason"] = msg
        return result

    context = build_weekly_report_context(today)
    html_content = render_to_string("cs_tasks/email_report.html", context)

    subject = f"【CS課題 週次レポート】{today.strftime('%Y/%m/%d')}"
    send_res = send_html_mail(
        subject=subject,
        html_body=html_content,
        recipients=recipients,
        account_code=MAIL_ACCOUNT_CODE,
    )

    if not send_res.get("sent"):
        result["reason"] = send_res.get("reason", "unknown error")
        logger.warning("[CSTASKS_MAIL] send_html_mail failed: %s", result["reason"])
        return result

    logger.info("[CSTASKS_MAIL] 週報送信完了: %s 件 → %s", len(recipients), recipients)
    result["sent"] = True
    return result
