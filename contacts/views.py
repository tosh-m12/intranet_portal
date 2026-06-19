from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request):
    """これまで会った相手先(人)を、来客(Visitor)+訪問(Meeting)から横断集約する。

    会社・姓・名 でひとり=1行に名寄せし、面会回数・役職(最新)をまとめる。
    会社名の昇順に並べる。読み取り専用。将来の名刺管理アプリの土台。
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

    return render(request, "contacts/index.html",
                  {"contacts": contacts, "total": len(contacts)})
