"""取引先マスタ + 既存請求明細を seed.json から投入する管理コマンド。

  python manage.py import_seed          # 既存データがあればスキップ
  python manage.py import_seed --force  # 既存があっても投入(重複注意)
"""
from django.core.management.base import BaseCommand

from billing.models import InvoiceLine, MasterParty
from billing.seedload import load_seed


class Command(BaseCommand):
    help = '取引先マスタと既存請求明細(seed.json)を投入する'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='既存データがあっても投入する')

    def handle(self, *args, **opts):
        n_m, n_l = load_seed(MasterParty, InvoiceLine, only_if_empty=not opts['force'])
        if n_m == 0 and n_l == 0:
            self.stdout.write('既存データありのためスキップ（--force で強制投入）。')
        else:
            self.stdout.write(self.style.SUCCESS(
                f'投入完了: 取引先マスタ {n_m} 件 / 請求明細 {n_l} 件'))
