"""初期データ(顧客マスタ + 既存トレーシング表)の投入ロジック。

`vessel_tracking/data/seed.json` を読み込んで Customer / Shipment を bulk_create する。
管理コマンド(import_seed)とデータマイグレーション(0002)の双方から呼ぶため、
モデルクラスを引数で受け取る(マイグレーションは historical model を渡す)。
"""
import datetime
import json
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'seed.json')

_DATE_FIELDS = ['order_received', 'etd', 'cy_cut', 'cy_open', 'cy_open_act',
                'vanning', 'eta', 'atd', 'ata', 'shanghai_ata', 'shanghai_eta']
_TEXT_FIELDS = ['order_no', 'inv_no', 'origin', 'dest', 'container_type',
                'vessel', 'voyage']
_NUM_FIELDS = ['out_ctn', 'out_m3', 'out_qty', 'container_count']


def _date(s):
    try:
        return datetime.date.fromisoformat(s[:10]) if s else None
    except (ValueError, TypeError):
        return None


def load_seed(Customer, Shipment, only_if_empty=True):
    """戻り値: (取込顧客数, 取込出荷数)。only_if_empty=True なら既存があればスキップ。"""
    if only_if_empty and (Customer.objects.exists() or Shipment.objects.exists()):
        return (0, 0)
    with open(DATA_PATH, encoding='utf-8') as f:
        data = json.load(f)

    cust_objs = {}
    for c in data.get('customers', []):
        obj, _created = Customer.objects.get_or_create(
            code=c['code'], defaults={'name': c.get('name', '')})
        cust_objs[c['code']] = obj

    # 履歴モデル(migration実行時)に存在するフィールドだけ渡す。
    # これにより、フィールド追加前の migration から呼ばれてもクラッシュしない。
    valid = {f.name for f in Shipment._meta.get_fields()}
    default_code = data.get('shipments_customer')
    rows = []
    for r in data.get('shipments', []):
        cust = cust_objs.get(r.get('customer') or default_code)
        kw = {'customer': cust, 'source': r.get('source') or 'ledger'}
        for k in _TEXT_FIELDS:
            kw[k] = (r.get(k) or '')
        for k in _NUM_FIELDS:
            kw[k] = r.get(k)
        for k in _DATE_FIELDS:
            kw[k] = _date(r.get(k))
        kw = {k: v for k, v in kw.items() if k in valid}
        rows.append(Shipment(**kw))
    Shipment.objects.bulk_create(rows)
    return (len(cust_objs), len(rows))
