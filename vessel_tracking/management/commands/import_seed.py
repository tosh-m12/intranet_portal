"""顧客マスタ + 既存トレーシング表を seed.json から投入する管理コマンド。

  python manage.py import_seed          # 既存データがあればスキップ
  python manage.py import_seed --force  # 既存があっても投入(重複注意)
"""
from django.core.management.base import BaseCommand

from vessel_tracking.models import Customer, Shipment
from vessel_tracking.seedload import load_seed


class Command(BaseCommand):
    help = '顧客マスタと既存トレーシング表(seed.json)を投入する'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='既存データがあっても投入する')

    def handle(self, *args, **opts):
        n_c, n_s = load_seed(Customer, Shipment, only_if_empty=not opts['force'])
        if n_c == 0 and n_s == 0:
            self.stdout.write('既存データありのためスキップ（--force で強制投入）。')
        else:
            self.stdout.write(self.style.SUCCESS(
                f'投入完了: 荷主マスタ {n_c} 件 / 出荷トレーシング {n_s} 件'))
