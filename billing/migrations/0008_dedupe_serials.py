"""重複してしまった連番を解消する一回限りのデータ修正。

採番が非アトミックだった時期に、同時保存で同一連番が割り当たったレコードが
存在する(例: 202511160705 が2件)。各重複グループで最古(最小id)を残し、
後発レコードを「同じ暦年の次の空き連番」に採番し直す。日付部(YYYYMMDD)は
元の請求日のまま保持する。重複が無ければ何もしない(冪等)。
"""
from collections import defaultdict

from django.db import migrations


def _year_suffix(s, year):
    if s and len(s) >= 12 and s[:8].isdigit() and s[8:].isdigit() and s[:4] == year:
        return int(s[8:])
    return None


def dedupe_serials(apps, schema_editor):
    InvoiceLine = apps.get_model('billing', 'InvoiceLine')

    groups = defaultdict(list)
    for o in InvoiceLine.objects.order_by('id'):
        if o.serial:
            groups[o.serial].append(o)

    # 暦年ごとの現在の最大連番(4桁)。空き番号採番の起点にする。
    max_by_year = {}
    for serial in groups:
        year = serial[:4]
        n = _year_suffix(serial, year)
        if n is not None:
            max_by_year[year] = max(max_by_year.get(year, 0), n)

    for serial, objs in groups.items():
        if len(objs) <= 1:
            continue
        # 最古(先頭)はそのまま。後発を次の空き番号へ。
        for o in objs[1:]:
            year = o.serial[:4]
            day = o.serial[:8]
            max_by_year[year] = max_by_year.get(year, 0) + 1
            o.serial = f'{day}{max_by_year[year]:04d}'
            o.save(update_fields=['serial'])


def noop(apps, schema_editor):
    # 連番の再付番は不可逆(元の重複状態には戻さない)。
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0007_seriallock'),
    ]

    operations = [
        migrations.RunPython(dedupe_serials, noop),
    ]
