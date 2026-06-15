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


@require_GET
def bridge_weekly(request):
    """週報: 社内側の集計(build_weekly_report_context)をそのまま JSON で返す。

    完了日(completed_at)・中止件数など Mac のスナップショットに無い情報も含むため、
    社内側で計算して返し、Mac は表示のみ行う(正本一致・ロジック重複なし)。"""
    if not _token_ok(request):
        return JsonResponse({"ok": False, "reason": "unauthorized"}, status=401)
    from ..email_utils import build_weekly_report_context
    from django.utils.timezone import localdate
    r = build_weekly_report_context(localdate())

    def _t(qs):
        return [{"title": t.title, "title_ja": t.title_ja, "client_name": t.client_name} for t in qs]

    def _d(qs):
        return [{"title": t.title, "title_ja": t.title_ja, "client_name": t.client_name,
                 "due_date": t.due_date.isoformat() if t.due_date else None} for t in qs]

    data = {
        "week_start": r["week_start"].isoformat(),
        "week_end": r["week_end"].isoformat(),
        "new_tasks": _t(r["new_tasks"]),
        "progressed_tasks": _t(r["progressed_tasks"]),
        "completed_tasks": _t(r["completed_tasks"]),
        "overdue_tasks": _d(r["overdue_tasks"]),
        "due_soon_tasks": _d(r["due_soon_tasks"]),
        "summary": r["summary"],
    }
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False})


@require_GET
def bridge_report_settings(request):
    """レポートメール設定一式(件名・本文 日中／翻訳状態／送信タイミング／宛先)を JSON で返す。
    Mac はこれを取得して、未翻訳なら相手言語へ翻訳して書き戻し、宛先/タイミングも編集できる。"""
    if not _token_ok(request):
        return JsonResponse({"ok": False, "reason": "unauthorized"}, status=401)
    from ..models import WeeklyReportConfig, WeeklyReportMailingList
    config, _ = WeeklyReportConfig.objects.get_or_create(pk=1)
    recipients = [
        {"name": e.name, "email": e.email, "is_active": e.is_active}
        for e in WeeklyReportMailingList.objects.all()
    ]
    return JsonResponse({
        "ok": True,
        "subject": config.subject or "",
        "body": config.body or "",
        "subject_zh": config.subject_zh or "",
        "body_zh": config.body_zh or "",
        "source_lang": config.source_lang or "ja",
        "translated": bool(config.translated),
        "mode": config.mode,
        "send_weekday": config.send_weekday,
        "send_time": config.send_time.strftime("%H:%M") if config.send_time else "",
        "recipients": recipients,
    }, json_dumps_params={"ensure_ascii": False})


@csrf_exempt
@require_POST
def bridge_report_settings_writeback(request):
    """Mac からの部分更新を受けて保存する。指定されたキーのみ更新:
      翻訳結果: subject / body / subject_zh / body_zh / translated
      送信設定: mode / send_weekday / send_time(HH:MM)
      宛先   : recipients = [{name,email,is_active}, ...]（全置換）"""
    if not _token_ok(request):
        return JsonResponse({"ok": False, "reason": "unauthorized"}, status=401)
    try:
        data = json.loads(request.body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return JsonResponse({"ok": False, "reason": f"JSON不正: {e}"}, status=400)
    from datetime import datetime as _dt
    from ..models import WeeklyReportConfig, WeeklyReportMailingList
    config, _ = WeeklyReportConfig.objects.get_or_create(pk=1)
    fields = []
    if "subject" in data:
        config.subject = (data.get("subject") or "")[:255]; fields.append("subject")
    if "body" in data:
        config.body = data.get("body") or ""; fields.append("body")
    if "subject_zh" in data:
        config.subject_zh = (data.get("subject_zh") or "")[:255]; fields.append("subject_zh")
    if "body_zh" in data:
        config.body_zh = data.get("body_zh") or ""; fields.append("body_zh")
    if "translated" in data:
        config.translated = bool(data.get("translated")); fields.append("translated")
    if "source_lang" in data and data.get("source_lang") in ("ja", "zh"):
        config.source_lang = data["source_lang"]; fields.append("source_lang")
    if "mode" in data and data.get("mode") in dict(WeeklyReportConfig.MODE_CHOICES):
        config.mode = data["mode"]; fields.append("mode")
    if "send_weekday" in data:
        try:
            wd = int(data["send_weekday"])
            if wd in dict(WeeklyReportConfig.WEEKDAY_CHOICES):
                config.send_weekday = wd; fields.append("send_weekday")
        except (TypeError, ValueError):
            pass
    if "send_time" in data and data.get("send_time"):
        try:
            config.send_time = _dt.strptime(data["send_time"], "%H:%M").time()
            fields.append("send_time")
        except ValueError:
            pass
    if fields:
        config.save(update_fields=fields)
    if isinstance(data.get("recipients"), list):
        WeeklyReportMailingList.objects.all().delete()
        for r in data["recipients"]:
            email = (r.get("email") or "").strip().lower()
            if email:
                WeeklyReportMailingList.objects.get_or_create(
                    email=email,
                    defaults={"name": r.get("name") or "", "is_active": bool(r.get("is_active", True))},
                )
        fields.append("recipients")
    logger.info("[BRIDGE_API] report settings 更新: %s", fields)
    return JsonResponse({"ok": True, "updated": fields}, json_dumps_params={"ensure_ascii": False})


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
