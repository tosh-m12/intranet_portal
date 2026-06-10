"""sales.db(財務部の売上)から sales_trend の seed.json を生成する(開発機で実行)。

分類ルール(引き継ぎ §7-1/§7-2 準拠):
  - category=Faurecia            → faurecia
  - category=大口顧客             → major
  - category=調整 or グレーID該当  → grey(調整・予估・外包)
  - 上記以外                      → other。開始年は「実際に初めて売上が立った年」で判定し、
                                    22年以前は 2022 に丸める(=その他22以前)。

本番(Windows)は sales.db を持たないため、本コマンドは Mac で実行して seed.json を
コミットし、本番へは migration 経由で投入する。
"""
import json
import os
import sqlite3

from django.core.management.base import BaseCommand, CommandError

# 引き継ぎ §7-1 のグレークラスタ固定ID(調整カテゴリ全行 + 以下)
GREY_IDS = {
    'C0003', 'C0218', 'C0219', 'C0220', 'C0221',
    'C0184', 'C0185', 'C0187', 'C0190', 'C0197', 'C0207', 'C0222',
}

DEFAULT_DB = '/Users/toshmurayama/NGLS-DB/sales_db/sales.db'
OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'seed.json'
)


def classify(category, code, first_year):
    if category == 'Faurecia':
        return 'faurecia', None
    if category == '大口顧客':
        return 'major', None
    if category == '調整' or code in GREY_IDS:
        return 'grey', None
    # その他: 実開始年で 22以前/各年 に振り分け
    if first_year is None or first_year <= 2022:
        return 'other', 2022
    return 'other', first_year


class Command(BaseCommand):
    help = 'sales.db から sales_trend/data/seed.json を生成する'

    def add_arguments(self, parser):
        parser.add_argument('--db', default=DEFAULT_DB, help='sales.db のパス')

    def handle(self, *args, **opts):
        db_path = opts['db']
        if not os.path.exists(db_path):
            raise CommandError(f'sales.db が見つかりません: {db_path}')

        con = sqlite3.connect(db_path)
        cat = {
            r[0]: r[1] for r in con.execute(
                'select cu.customer_id, c.name from customers cu '
                'join categories c on cu.category_id = c.category_id'
            )
        }
        meta = {
            r[0]: (r[1], r[2]) for r in con.execute(
                'select customer_id, group_name, customer_name from customers'
            )
        }
        first_year = {
            r[0]: r[1] for r in con.execute(
                'select customer_id, min(year) from sales group by customer_id'
            )
        }

        customers = []
        for code, category in cat.items():
            klass, osy = classify(category, code, first_year.get(code))
            grp, name = meta.get(code, ('', ''))
            customers.append({
                'code': code, 'group_name': grp or '', 'customer_name': name or '',
                'klass': klass, 'other_start_year': osy,
            })

        sales = [
            {'code': r[0], 'year': r[1], 'month': r[2], 'amount': round(r[3], 2)}
            for r in con.execute('select customer_id, year, month, amount from sales')
        ]
        con.close()

        payload = {
            'source': os.path.basename(db_path),
            'customers': sorted(customers, key=lambda c: c['code']),
            'sales': sales,
        }
        with open(OUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=0)

        from collections import Counter
        kc = Counter(c['klass'] for c in customers)
        self.stdout.write(self.style.SUCCESS(
            f'seed.json 生成: 顧客{len(customers)} 売上{len(sales)} '
            f'/ 系列 {dict(kc)} → {OUT_PATH}'
        ))
