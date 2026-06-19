import json
from collections import Counter

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

# インライン編集で裏に反映する対象。会社名/姓/名/役職のみ許可。
EDITABLE_FIELDS = {"company_name", "last_name", "first_name", "title"}


def _is_staff(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


@login_required
def index(request):
    """これまで会った相手先(人)を、来客(Visitor)+訪問(Meeting)から横断集約する。

    会社・姓・名 でひとり=1行に名寄せし、面会回数・役職(最新)をまとめる。会社名昇順。
    姓+名が同一の相手先は「同一人物の疑い」として dup フラグを立てる(クリーニングの手掛かり)。
    staff/superuser はセルを編集でき、裏の Visitor/Meeting レコードに反映される。
    """
    from visitors.models import Visitor
    from meetings.models import Meeting

    agg = {}

    def collect(rows):
        for r in rows:
            co = (r["company_name"] or "").strip()
            ln = (r["last_name"] or "").strip()
            fn = (r["first_name"] or "").strip()
            ti = (r["title"] or "").strip()
            d = r["visit_date"]
            if not (ln or fn):          # 名前の無い行は「人の一覧」に出さない
                continue
            key = (co, ln, fn)
            e = agg.get(key)
            if e is None:
                e = {"company": co, "last": ln, "first": fn, "title": ti,
                     "count": 0, "_tdate": d if ti else None}
                agg[key] = e
            e["count"] += 1
            # 役職は最新(面会日が新しい方)を採用
            if ti and d and (e["_tdate"] is None or d >= e["_tdate"]):
                e["title"] = ti
                e["_tdate"] = d

    fields = ("company_name", "last_name", "first_name", "title", "visit_date")
    collect(Visitor.objects.filter(cancelled=False).values(*fields))
    collect(Meeting.objects.filter(cancelled=False).values(*fields))

    contacts = sorted(agg.values(),
                      key=lambda e: (e["company"], e["last"], e["first"]))

    # 同名(姓+名)が2件以上 → 同一人物の疑い
    name_counts = Counter((e["last"], e["first"]) for e in contacts)
    for e in contacts:
        e["dup"] = name_counts[(e["last"], e["first"])] > 1

    return render(request, "contacts/index.html", {
        "contacts": contacts,
        "total": len(contacts),
        "can_edit": _is_staff(request.user),
    })


@login_required
@require_POST
def inline_update(request):
    """相手先名簿のセル編集 → 集約キー(会社・姓・名)に一致する Visitor/Meeting を一括更新。

    body: {"company","last","first","field","value"}。staff/superuser のみ。
    field は EDITABLE_FIELDS のみ。会社名を直せば英/日表記が次回読み込みで1行に統合される。
    """
    from visitors.models import Visitor
    from meetings.models import Meeting

    if not _is_staff(request.user):
        return JsonResponse(
            {"ok": False, "error": _("編集する権限がありません。")}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON不正"}, status=400)

    field = data.get("field")
    if field not in EDITABLE_FIELDS:
        return JsonResponse({"ok": False, "error": "編集不可の項目です。"}, status=400)

    value = (data.get("value") or "").strip()
    flt = {
        "cancelled": False,
        "company_name": (data.get("company") or "").strip(),
        "last_name": (data.get("last") or "").strip(),
        "first_name": (data.get("first") or "").strip(),
    }
    updated = (Visitor.objects.filter(**flt).update(**{field: value})
               + Meeting.objects.filter(**flt).update(**{field: value}))

    return JsonResponse({"ok": True, "value": value, "updated": updated})
