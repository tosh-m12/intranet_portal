"""初期データ(顧客分類 + 月別売上)の投入ロジック。

`sales_trend/data/seed.json` を読み込んで Customer / MonthlySale を bulk_create する。
管理コマンド(build_seed が生成 → import_seed が投入)とデータマイグレーション(0002)
の双方から呼ぶため、モデルクラスを引数で受け取る。
"""
import json
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'seed.json')


def load_seed(Customer, MonthlySale, only_if_empty=True):
    """戻り値: (顧客数, 売上明細数)。only_if_empty=True なら既存があればスキップ。"""
    if only_if_empty and (Customer.objects.exists() or MonthlySale.objects.exists()):
        return (0, 0)
    with open(DATA_PATH, encoding='utf-8') as f:
        data = json.load(f)

    customers = [
        Customer(code=c['code'], group_name=c.get('group_name') or '',
                 customer_name=c.get('customer_name') or '', klass=c['klass'],
                 other_start_year=c.get('other_start_year'))
        for c in data['customers']
    ]
    Customer.objects.bulk_create(customers, ignore_conflicts=True)
    by_code = {c.code: c for c in Customer.objects.all()}

    sales = [
        MonthlySale(customer=by_code[s['code']], year=s['year'],
                    month=s['month'], amount=s['amount'])
        for s in data['sales'] if s['code'] in by_code
    ]
    MonthlySale.objects.bulk_create(sales)
    return (len(customers), len(sales))
