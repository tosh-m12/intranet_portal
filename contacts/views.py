from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request):
    """これまで会った相手先(人)を、来客(Visitor)+訪問(Meeting)から横断集約する。

    会社・姓・名 でひとり=1行に名寄せし、種別(来客/訪問/両方)・最終面会日・
    面会回数・役職(最新)をまとめる。読み取り専用。将来の名刺管理アプリの土台。
    """
    from visitors.models import Visitor
    from meetings.models import Meeting

    agg = {}

    def collect(rows, kind):
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
                     "kinds": set(), "last_date": d, "count": 0, "_tdate": d if ti else None}
                agg[key] = e
            e["count"] += 1
            e["kinds"].add(kind)
            if d and (e["last_date"] is None or d > e["last_date"]):
                e["last_date"] = d
            if ti and d and (e["_tdate"] is None or d >= e["_tdate"]):
                e["title"] = ti
                e["_tdate"] = d

    fields = ("company_name", "last_name", "first_name", "title", "visit_date")
    collect(Visitor.objects.filter(cancelled=False).values(*fields), "来客")
    collect(Meeting.objects.filter(cancelled=False).values(*fields), "訪問")

    contacts = sorted(
        agg.values(),
        key=lambda e: (e["last_date"] or date.min, e["company"], e["last"]),
        reverse=True,
    )
    for e in contacts:
        e["kind_label"] = "両方" if len(e["kinds"]) > 1 else next(iter(e["kinds"]))

    return render(request, "contacts/index.html",
                  {"contacts": contacts, "total": len(contacts)})
