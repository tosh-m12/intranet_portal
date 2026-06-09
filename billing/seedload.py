"""初期データ(取引先マスタ + 既存請求明細)の投入ロジック。

`billing/data/seed.json` を読み込んで MasterParty / InvoiceLine を bulk_create する。
管理コマンド(import_seed)とデータマイグレーション(0002)の双方から呼ぶため、
モデルクラスを引数で受け取る(マイグレーションは historical model を渡す)。
"""
import datetime
import json
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'seed.json')

_FEE = ['storage', 'warehouse', 'customs', 'service', 'loc_transport', 'disb', 'oth', 'tpl']
_LINE_FIELDS = (['serial', 'customer_gc', 'bill_to', 'bill_cat', 'currency',
                 'bill_year', 'bill_month', 'assignee']
                + _FEE + [f + '_rate' for f in _FEE]
                + ['total_before_tax', 'total_after_tax', 'exrate', 'fx_currency',
                   'converted_amount'])
_TEXT = {'customer_gc', 'bill_to', 'bill_cat', 'currency', 'assignee', 'serial', 'fx_currency'}


def _date(s):
    try:
        return datetime.date.fromisoformat(s[:10]) if s else None
    except (ValueError, TypeError):
        return None


def load_seed(MasterParty, InvoiceLine, only_if_empty=True):
    """戻り値: (取込マスタ数, 取込明細数)。only_if_empty=True なら既存データがあればスキップ。"""
    if only_if_empty and (MasterParty.objects.exists() or InvoiceLine.objects.exists()):
        return (0, 0)
    with open(DATA_PATH, encoding='utf-8') as f:
        data = json.load(f)

    masters = [MasterParty(group_name=m['group_name'], company_name=m['company_name'],
                           assignee=m.get('assignee') or '') for m in data['master']]
    MasterParty.objects.bulk_create(masters, ignore_conflicts=True)

    lines = []
    for r in data['invoices']:
        kw = {}
        for k in _LINE_FIELDS:
            v = r.get(k)
            kw[k] = (v or '') if k in _TEXT else v
        kw['rate_date'] = _date(r.get('rate_date'))
        kw['source'] = r.get('source') or 'ledger'
        lines.append(InvoiceLine(**kw))
    InvoiceLine.objects.bulk_create(lines)
    return (len(masters), len(lines))
