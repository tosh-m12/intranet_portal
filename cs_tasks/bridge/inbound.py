# cs_tasks/bridge/inbound.py
"""復路(Mac→社内)の書き戻しメールをDBへ反映する。

検証は2段: (1) 差出人限定 → (2) HMAC署名。さらに nonce でメール単位の
リプレイを、op_id で操作単位の二重適用を防ぐ(いずれも冪等)。

再送モデル: 各メールは一意の nonce を持つ(=メール単位のリプレイ防止)。
失敗 op を再適用したい場合は、同じ op_id を新しい nonce のメールで再送する。
適用済み op_id はスキップされ、未適用のものだけが反映される。
"""
import logging
from datetime import date, datetime
from email.utils import parseaddr

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .. import models as m
from . import payload as pl
from . import security

logger = logging.getLogger(__name__)
User = get_user_model()

VALID_ACTIONS = {
    "add_comment", "edit_progress", "edit_task", "add_task", "edit_comment",
    "add_progress", "delete",
}

# delete action の target → 削除方式
# Task は既存運用に合わせて論理削除(is_cancelled=True)。
# ProgressUpdate / SupervisorComment は物理削除。
_DELETE_TARGETS = {"task", "progress", "comment"}

# edit_task / add_task の fields キー → Task の属性名
_TASK_FIELD_MAP = {
    "title_zh": "title",
    "title_ja": "title_ja",
    "description_zh": "description",
    "description_ja": "description_ja",
    "client_name": "client_name",
    "category": "category",
}


def _bridge_author():
    email = getattr(settings, "CS_BRIDGE_AUTHOR_EMAIL", "") or ""
    if not email:
        return None
    return User.objects.filter(email=email).first()


def _resolve_user(email):
    if not email:
        return None
    return User.objects.filter(email=email).first()


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def _sender_allowed(sender):
    allow = [a.lower() for a in getattr(settings, "CS_BRIDGE_ALLOWED_SENDERS", []) if a]
    if not allow:
        # 差出人限定が未設定なら、HMAC のみで判定(差出人チェックはスキップ)
        return True
    addr = parseaddr(sender or "")[1].lower()
    return bool(addr) and addr in allow


def _apply_task_fields(task, fields):
    for src, dst in _TASK_FIELD_MAP.items():
        if src in fields and fields[src] is not None:
            setattr(task, dst, fields[src])
    if "due_date" in fields:
        task.due_date = _parse_date(fields.get("due_date"))
    if "assignee_email" in fields:
        task.assignee = _resolve_user(fields.get("assignee_email"))


def _resolve_task_id(op, created_map):
    """add_progress 等の対象課題IDを解決する。

    task_id があればそれ。無ければ task_ref(=add_task の op_id)から、
    同一メール内で作成した課題(created_map)→過去メールで作成した課題
    (BridgeProcessedOperation.result_task_id)の順に引く。
    """
    tid = op.get("task_id")
    if tid:
        return tid
    ref = op.get("task_ref")
    if ref:
        if ref in created_map:
            return created_map[ref]
        rec = m.BridgeProcessedOperation.objects.filter(op_id=ref).first()
        if rec and rec.result_task_id:
            return rec.result_task_id
    return None


def _apply_op(op, author, created_map=None):
    """op を1件適用する。add_task のときは生成した Task の id を返す。"""
    action = op["action"]
    created_map = created_map if created_map is not None else {}

    if action == "add_comment":
        progress = m.ProgressUpdate.objects.get(pk=op["progress_id"])
        m.SupervisorComment.objects.create(
            progress=progress,
            author=author,
            content=op.get("content_zh") or "",
            content_ja=op.get("content_ja") or "",
        )
        # 子の変更を往路差分スナップショットに載せるため親課題を touch
        progress.task.save(update_fields=["updated_at"])

    elif action == "add_progress":
        # Mac(上長)が新規課題等に進捗を追加する。中文(content_zh)必須・日本語訳は任意。
        # task_id 直接指定 or task_ref(同一/過去メールの add_task)から課題を解決。
        tid = _resolve_task_id(op, created_map)
        if not tid:
            raise ValueError("add_progress: task_id/task_ref から課題を解決できません。")
        task = m.Task.objects.get(pk=tid)
        progress = m.ProgressUpdate(
            task=task,
            author=author,
            content=op.get("content_zh") or "",
            content_ja=op.get("content_ja") or "",
        )
        ed = _parse_date(op.get("execution_date"))
        if ed:
            progress.execution_date = ed
        progress.save()
        # 子の追加を往路差分スナップショットに載せるため親課題を touch
        task.save(update_fields=["updated_at"])

    elif action == "edit_progress":
        progress = m.ProgressUpdate.objects.get(pk=op["progress_id"])
        upd = []
        if "content_zh" in op:
            progress.content = op.get("content_zh") or ""
            upd.append("content")
        if "content_ja" in op:
            progress.content_ja = op.get("content_ja") or ""
            upd.append("content_ja")
        if "execution_date" in op:
            progress.execution_date = _parse_date(op.get("execution_date"))
            upd.append("execution_date")
        if upd:
            progress.save(update_fields=upd)
            progress.task.save(update_fields=["updated_at"])

    elif action == "edit_comment":
        comment = m.SupervisorComment.objects.get(pk=op["comment_id"])
        if "content_zh" in op:
            comment.content = op.get("content_zh") or ""
        if "content_ja" in op:
            comment.content_ja = op.get("content_ja") or ""
        comment.save(update_fields=["content", "content_ja"])
        comment.progress.task.save(update_fields=["updated_at"])

    elif action == "edit_task":
        task = m.Task.objects.get(pk=op["task_id"])
        _apply_task_fields(task, op.get("fields") or {})
        task.save()

    elif action == "add_task":
        task = m.Task(owner=author)
        _apply_task_fields(task, op.get("fields") or {})
        if not (task.title or "").strip():
            raise ValueError("add_task には title(title_zh) が必要です。")
        task.save()
        # 同一メール内の後続 add_progress(task_ref) が参照できるよう記録
        created_map[op["op_id"]] = task.id
        return task.id

    elif action == "delete":
        target = op.get("target")
        target_id = op.get("id")
        if target not in _DELETE_TARGETS:
            raise ValueError(f"delete の target が不正: {target!r}")
        if not isinstance(target_id, int):
            raise ValueError("delete には id(int) が必要です。")

        if target == "task":
            # 既存の論理削除運用に合わせる。往路スナップショットは
            # is_cancelled=False のみ送るため、以後 Mac からも消える。
            m.Task.objects.filter(pk=target_id).update(
                is_cancelled=True,
                cancelled_at=timezone.now(),
            )
        elif target == "progress":
            # 削除前に親課題を控え、削除後に touch（差分スナップショットに
            # 子集合の減少を載せて Mac 側の権威的置換で消えるようにする）。
            progress = m.ProgressUpdate.objects.filter(pk=target_id).first()
            parent_task = progress.task if progress else None
            if progress:
                progress.delete()
            if parent_task:
                parent_task.save(update_fields=["updated_at"])
        elif target == "comment":
            comment = m.SupervisorComment.objects.filter(pk=target_id).first()
            parent_task = comment.progress.task if comment else None
            if comment:
                comment.delete()
            if parent_task:
                parent_task.save(update_fields=["updated_at"])
        # 対象が無くても黙って no-op。op_id 冪等で重複適用は防がれる。


def apply_writeback(payload, signature, sender=None, raw_text=None, enforce_sender=True):
    """検証済みの書き戻しを適用する。結果サマリ(dict)を返す。

    raw_text を渡すと、監査用に受信本文原文を BridgeProcessedMessage に保存する
    （社内側はメール削除運用のため、原文を DB に残して追跡できるようにする）。

    enforce_sender=False のときは差出人許可チェックを省く。API(Bearerトークン認証)
    経由の呼び出しは、メールの差出人許可リストの代わりにトークンで認証するため。
    """
    result = {
        "ok": False,
        "reason": "",
        "applied": [],
        "skipped": [],
        "errors": [],
    }

    if not isinstance(payload, dict):
        result["reason"] = "payload が見つかりません。"
        return result

    if payload.get("schema") not in pl.SUPPORTED_INBOUND_SCHEMAS:
        result["reason"] = f"未対応のschema: {payload.get('schema')}"
        return result

    # (1) 差出人限定(API経由は enforce_sender=False でスキップ)
    if enforce_sender and not _sender_allowed(sender):
        result["reason"] = "許可されていない差出人です。"
        logger.warning("[CSBRIDGE] rejected sender=%r", sender)
        return result

    # (2) HMAC署名検証
    if not security.verify(payload, signature):
        result["reason"] = "署名検証に失敗しました。"
        logger.warning("[CSBRIDGE] signature verification failed")
        return result

    nonce = payload.get("nonce")
    if not nonce:
        result["reason"] = "nonce がありません。"
        return result

    # メール単位のリプレイ防止(処理済みなら冪等にスキップ)
    if m.BridgeProcessedMessage.objects.filter(nonce=nonce).exists():
        result["ok"] = True
        result["reason"] = "処理済みメッセージ(重複)。"
        return result

    ops = payload.get("ops") or []
    author = _bridge_author()
    created_map = {}   # 同一メール内: add_task の op_id -> 生成 task_id（task_ref 解決用）

    for op in ops:
        op_id = op.get("op_id")
        action = op.get("action")
        if not op_id or not action:
            result["errors"].append({"op": op, "error": "op_id/action が不足。"})
            continue
        if action not in VALID_ACTIONS:
            result["errors"].append({"op_id": op_id, "error": f"未知のaction: {action}"})
            continue
        # 操作単位の冪等性
        if m.BridgeProcessedOperation.objects.filter(op_id=op_id).exists():
            result["skipped"].append(op_id)
            continue
        try:
            # 各opはsavepointで囲み、失敗時はそのopだけロールバック
            with transaction.atomic():
                result_task_id = _apply_op(op, author, created_map)
                m.BridgeProcessedOperation.objects.create(
                    op_id=op_id, action=action, result_task_id=result_task_id
                )
        except Exception as e:  # noqa: BLE001
            logger.exception("[CSBRIDGE] op failed: %s", op_id)
            result["errors"].append({"op_id": op_id, "error": str(e)})
            continue
        result["applied"].append(op_id)

    # メールを処理済みとして記録(以後この nonce は再適用しない)。原文も監査用に保存。
    m.BridgeProcessedMessage.objects.create(
        nonce=nonce, sender=sender or "", raw_body=raw_text or ""
    )

    result["ok"] = True
    return result


def apply_writeback_text(raw_text, sender=None):
    """メール本文テキストから payload/署名を抽出して適用する。"""
    payload, signature = pl.extract_writeback(raw_text)
    return apply_writeback(payload, signature, sender=sender, raw_text=raw_text)
