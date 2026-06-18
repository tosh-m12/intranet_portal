# opsbridge/bridge_api.py
"""汎用メンテナンス用 export/writeback API(Cloudflare Tunnel 経由)。

Mac から本番DBの「ホワイトリストされた」モデルを読取・書戻しするための恒久API。

  POST /ops/api/export     {"model","fields"?,"filters"?} → values() を JSON で返す
  POST /ops/api/writeback  {"payload":{nonce,model,updates[],dry_run?}, "signature"} → 適用

認証(多層・既存ブリッジと同一):
  1. Bearer トークン(settings.CS_BRIDGE_API_TOKEN)。未設定はフェイルクローズ。
  2. writeback は HMAC 署名(cs_tasks.bridge.security.verify, CS_BRIDGE_HMAC_SECRET)。
  3. 本番は更に Cloudflare Access(Service Token)をエッジで重ねる。

許可は settings.OPSBRIDGE_EXPORT_MODELS / OPSBRIDGE_WRITEBACK_MODELS で限定。
"""
import hmac
import json
import logging
from datetime import date, datetime, time

from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from cs_tasks.bridge import security  # HMAC は既存ユーティリティを再利用

logger = logging.getLogger(__name__)


def _token_ok(request):
    """Authorization: Bearer <token> を定数時間比較で検証。トークン未設定は常に拒否。"""
    expected = getattr(settings, "CS_BRIDGE_API_TOKEN", "") or ""
    if not expected:
        return False
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth.startswith("Bearer "):
        return False
    presented = auth[len("Bearer "):].strip()
    return hmac.compare_digest(presented, expected)


def _resolve_model(label, allowed):
    """'app.Model' を解決。許可集合に無ければ None。"""
    if label not in allowed:
        return None
    try:
        app_label, model_name = label.split(".", 1)
        return apps.get_model(app_label, model_name)
    except (ValueError, LookupError):
        return None


def _concrete_field_names(model):
    return [f.name for f in model._meta.concrete_fields]


def _jsonable(v):
    """date/time 等を監査JSON(JSONField)用に文字列化。ORM 属性は生の型のため。"""
    if isinstance(v, (date, time, datetime)):
        return v.isoformat()
    return v


# ---------- export ----------
@csrf_exempt
@require_POST
def ops_export(request):
    """{"model","fields"?,"filters"?} を受け、values() を JSON で返す(読取専用)。"""
    if not _token_ok(request):
        return JsonResponse({"ok": False, "reason": "unauthorized"}, status=401)
    try:
        data = json.loads(request.body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return JsonResponse({"ok": False, "reason": f"JSON不正: {e}"}, status=400)

    label = data.get("model")
    model = _resolve_model(label, getattr(settings, "OPSBRIDGE_EXPORT_MODELS", set()))
    if model is None:
        return JsonResponse(
            {"ok": False, "reason": f"export 非許可モデル: {label!r}"}, status=403)

    all_fields = _concrete_field_names(model)
    fields = data.get("fields") or all_fields
    unknown = [f for f in fields if f not in all_fields]
    if unknown:
        return JsonResponse(
            {"ok": False, "reason": f"未知のフィールド: {unknown}"}, status=400)

    filters = data.get("filters") or {}
    # 単純等価のみ。フィールド名でホワイトリスト(__lookup を禁止して安全側に)。
    bad = [k for k in filters if k not in all_fields]
    if bad:
        return JsonResponse(
            {"ok": False, "reason": f"filters の不正キー: {bad}"}, status=400)

    qs = model.objects.filter(**filters) if filters else model.objects.all()
    rows = list(qs.values(*fields))
    return JsonResponse(
        {"ok": True, "model": label, "schema": list(fields),
         "count": len(rows), "rows": rows},
        json_dumps_params={"ensure_ascii": False},
    )


# ---------- writeback ----------
@csrf_exempt
@require_POST
def ops_writeback(request):
    """{"payload":{nonce,model,updates[],dry_run?}, "signature"} を適用。

    payload.updates = [{"pk":1, "fields":{...許可フィールドのみ...}}, ...]
    各更新の前後を OpsAuditLog に記録。nonce 冪等。dry_run は差分計算のみ。
    """
    from .models import OpsAuditLog, OpsProcessedMessage

    if not _token_ok(request):
        return JsonResponse({"ok": False, "reason": "unauthorized"}, status=401)

    raw = request.body.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return JsonResponse({"ok": False, "reason": f"JSON不正: {e}"}, status=400)

    payload = data.get("payload")
    signature = data.get("signature")
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "reason": "payload がありません。"}, status=400)

    # (1) HMAC 署名(鍵未設定/不一致は False=フェイルクローズ)
    if not security.verify(payload, signature):
        logger.warning("[OPSBRIDGE] signature verification failed")
        return JsonResponse({"ok": False, "reason": "署名検証に失敗しました。"}, status=400)

    nonce = payload.get("nonce")
    if not nonce:
        return JsonResponse({"ok": False, "reason": "nonce がありません。"}, status=400)

    label = payload.get("model")
    allowed_map = getattr(settings, "OPSBRIDGE_WRITEBACK_MODELS", {})
    if label not in allowed_map:
        return JsonResponse(
            {"ok": False, "reason": f"writeback 非許可モデル: {label!r}"}, status=403)
    model = _resolve_model(label, set(allowed_map.keys()))
    if model is None:
        return JsonResponse(
            {"ok": False, "reason": f"モデル解決失敗: {label!r}"}, status=400)
    allowed_fields = set(allowed_map[label])

    dry_run = bool(payload.get("dry_run"))
    updates = payload.get("updates") or []

    # nonce 冪等(dry_run は記録しないので本適用のみチェック)
    if not dry_run and OpsProcessedMessage.objects.filter(nonce=nonce).exists():
        return JsonResponse(
            {"ok": True, "reason": "処理済みリクエスト(重複)。",
             "dry_run": False, "applied": [], "skipped": [], "errors": [], "diffs": []},
            json_dumps_params={"ensure_ascii": False})

    result = {"ok": True, "dry_run": dry_run, "model": label,
              "applied": [], "skipped": [], "errors": [], "diffs": []}

    try:
        with transaction.atomic():
            for upd in updates:
                pk = upd.get("pk")
                fields = upd.get("fields") or {}
                bad = [f for f in fields if f not in allowed_fields]
                if bad:
                    result["errors"].append({"pk": pk, "error": f"許可外フィールド: {bad}"})
                    continue
                obj = model.objects.filter(pk=pk).first()
                if obj is None:
                    result["errors"].append({"pk": pk, "error": "対象が存在しません。"})
                    continue

                before = {f: getattr(obj, f) for f in fields}
                changed = {f: fields[f] for f in fields if before[f] != fields[f]}
                if not changed:
                    result["skipped"].append(pk)
                    continue

                result["diffs"].append(
                    {"pk": pk,
                     "before": {f: _jsonable(before[f]) for f in changed},
                     "after": {f: _jsonable(changed[f]) for f in changed}})

                if not dry_run:
                    for f, v in changed.items():
                        setattr(obj, f, v)
                    obj.save(update_fields=list(changed.keys()))
                    OpsAuditLog.objects.create(
                        model_label=label, target_pk=str(pk), action="update",
                        before_json={f: _jsonable(before[f]) for f in changed},
                        after_json={f: _jsonable(changed[f]) for f in changed},
                        actor="api:ops-bridge",
                    )
                    result["applied"].append(pk)

            if not dry_run:
                OpsProcessedMessage.objects.create(nonce=nonce, raw_body=raw)
            else:
                # 差分計算だけして書かない。savepoint を明示ロールバック。
                transaction.set_rollback(True)
    except Exception as e:  # noqa: BLE001
        logger.exception("[OPSBRIDGE] writeback failed")
        return JsonResponse({"ok": False, "reason": str(e)}, status=400)

    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
