# cs_tasks/bridge/api.py
"""リアルタイム連携API(メール往復の置換)。

Mac(cs_bridge)が Cloudflare Tunnel 越しにこの2本を叩く:
  GET  cs-tasks/bridge/api/sync?since=<ISO>   往路: スナップショットJSONを返す
  POST cs-tasks/bridge/api/writeback          復路: {payload, signature} を受けて適用

認証(多層):
  1. Bearerトークン(settings.CS_BRIDGE_API_TOKEN)。未設定ならフェイルクローズ(全拒否)。
  2. writeback は従来どおり HMAC 署名検証(inbound.apply_writeback 内)。
  3. 本番は更に Cloudflare Access(Service Token)をエッジで重ねる。

メール経路(outbound/inbound + cs_sync_send/cs_inbound_poll)とは独立。中身の生成・適用は
build_snapshot / apply_writeback を再利用するため、契約(payload/HMAC/op冪等)は不変。
"""
import hmac
import json
import logging
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import inbound, outbound

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


@require_GET
def bridge_sync(request):
    """往路: 非中止課題のスナップショットを JSON で返す。?since=<ISO8601> で差分。"""
    if not _token_ok(request):
        return JsonResponse({"ok": False, "reason": "unauthorized"}, status=401)
    since = None
    raw_since = request.GET.get("since")
    if raw_since:
        since = parse_datetime(raw_since)
        if since is None:
            return JsonResponse({"ok": False, "reason": "since が不正(ISO8601)"}, status=400)
    snapshot = outbound.build_snapshot(since=since)
    return JsonResponse(snapshot, json_dumps_params={"ensure_ascii": False})


@csrf_exempt
@require_POST
def bridge_writeback(request):
    """復路: {payload, signature} を受けて apply_writeback で適用。結果を JSON で返す。"""
    if not _token_ok(request):
        return JsonResponse({"ok": False, "reason": "unauthorized"}, status=401)
    raw = request.body.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return JsonResponse({"ok": False, "reason": f"JSON不正: {e}"}, status=400)
    payload = data.get("payload")
    signature = data.get("signature")
    # 差出人許可リストはメール用。API は Bearerトークンで認証済みのため enforce_sender=False。
    result = inbound.apply_writeback(
        payload, signature, sender="api:cs-bridge", raw_text=raw, enforce_sender=False
    )
    status = 200 if result.get("ok") else 400
    return JsonResponse(result, status=status, json_dumps_params={"ensure_ascii": False})
