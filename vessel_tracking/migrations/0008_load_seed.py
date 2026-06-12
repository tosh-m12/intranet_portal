"""初期データ投入: 荷主マスタ + 既存トレーシング表(seed.json)。

全フィールドが揃った後(0007 の後)に実投入する。本番でも `migrate` 時に自動投入される。
既存データがあればスキップ(冪等)。
"""
from django.db import migrations


def load(apps, schema_editor):
    from vessel_tracking.seedload import load_seed
    Customer = apps.get_model('vessel_tracking', 'Customer')
    Shipment = apps.get_model('vessel_tracking', 'Shipment')
    load_seed(Customer, Shipment, only_if_empty=True)


def unload(apps, schema_editor):
    # ロールバック時は取込分(ledger)のみ削除。手入力分・荷主マスタは保持。
    apps.get_model('vessel_tracking', 'Shipment').objects.filter(source='ledger').delete()


class Migration(migrations.Migration):
    dependencies = [('vessel_tracking', '0007_shipment_container_count')]
    operations = [migrations.RunPython(load, unload)]
