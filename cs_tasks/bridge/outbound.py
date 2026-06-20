# cs_tasks/bridge/outbound.py
"""往路(社内→Mac)の同期スナップショットを組み立てる。

Mac側(cs_bridge)は HTTP API(bridge_sync)経由でこのスナップショットを取得し、
中国語を日本語へ翻訳してレビュー画面に表示する。ID(task_id/progress_id/comment_id)
を含めるため、後続の書き戻しで対象を一意に参照できる。
(メール送信経路は撤去済み。build_snapshot は api.bridge_sync が再利用する。)
"""
import logging
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.timezone import localtime

from .. import models as m
from . import payload as pl

logger = logging.getLogger(__name__)


def _iso(dt):
    return localtime(dt).isoformat() if dt else None


def _user_label(user):
    if user is None:
        return None
    last = (getattr(user, "last_name", "") or "").strip()
    first = (getattr(user, "first_name", "") or "").strip()
    name = (last + " " + first).strip()
    return name or user.get_username()


def _build_assignees():
    """Mac 側で担当者ドロップダウンに使う候補リスト。
    is_active=True かつ superuser を除く(社内 UI と同方針)。
    """
    User = get_user_model()
    qs = (
        User.objects.filter(is_active=True, is_superuser=False)
        .order_by("display_order", "last_name", "first_name", "email")
    )
    return [
        {
            "email": u.email,
            "display_name": _user_label(u),
            "is_staff": bool(u.is_staff),
            "display_order": u.display_order,
        }
        for u in qs
    ]


def build_snapshot(since=None):
    """非表示でない(active)課題スナップショットを dict で返す。

    since(datetime)を渡すと、since 以降に更新があった課題のみに絞る
    (課題自体の更新 or 進捗/コメントの新規記入)。None なら全件。
    """
    qs = (
        m.Task.objects.filter(is_hidden=False)
        .select_related("owner", "assignee")
        .prefetch_related("progress_updates__author", "progress_updates__comments")
        .order_by("created_at", "id")
    )
    if since is not None:
        qs = qs.filter(
            Q(updated_at__gte=since)
            | Q(progress_updates__created_at__gte=since)
            | Q(progress_updates__comments__created_at__gte=since)
        ).distinct()

    tasks = []
    for t in qs:
        progress_list = []
        for p in t.progress_updates.all():
            comments = [
                {
                    "id": c.id,
                    "author": _user_label(c.author),
                    "content": c.content,
                    "content_ja": c.content_ja,
                    "created_at": _iso(c.created_at),
                }
                for c in p.comments.all()
            ]
            progress_list.append(
                {
                    "id": p.id,
                    "author": _user_label(p.author),
                    "content": p.content,
                    "content_ja": p.content_ja,
                    "created_at": _iso(p.created_at),
                    # 実施日（未設定なら null。表示側は created_at にフォールバック）
                    "execution_date": p.execution_date.isoformat() if p.execution_date else None,
                    "is_closed": p.is_closed,
                    "comments": comments,
                }
            )
        tasks.append(
            {
                "id": t.id,
                "category": t.category,
                "title": t.title,
                "title_ja": t.title_ja,
                "description": t.description,
                "description_ja": t.description_ja,
                "client_name": t.client_name,
                "assignee": _user_label(t.assignee),
                "assignee_email": getattr(t.assignee, "email", None),
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "is_closed": t.is_closed,
                "created_at": _iso(t.created_at),
                "updated_at": _iso(t.updated_at),
                "progress_updates": progress_list,
            }
        )

    now = localtime()
    return {
        "type": "snapshot",
        "schema": pl.SCHEMA_VERSION,
        "seq": int(now.timestamp()),
        "generated_at": now.isoformat(),
        "since": _iso(since) if since else None,
        "meta": {
            # Mac 側で add_task / edit_task の担当者ドロップダウンに使う
            "assignees": _build_assignees(),
            # 現存(非表示でない)課題IDの“全件”。since で tasks を差分に絞っても、これは
            # 常に全件入れる。Mac はこのリストに無い課題を state から除去することで、
            # 非表示/削除された課題が差分スナップショットだけでも消える（追従）。
            "active_task_ids": list(
                m.Task.objects.filter(is_hidden=False)
                .order_by("id")
                .values_list("id", flat=True)
            ),
        },
        "tasks": tasks,
    }
