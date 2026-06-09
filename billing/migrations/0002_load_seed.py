"""初期データ投入: 取引先マスタ + 既存請求明細(seed.json)。

本番でも `migrate` 実行時に自動投入される。既存データがあればスキップ(冪等)。
"""
from django.db import migrations


def load(apps, schema_editor):
    from billing.seedload import load_seed
    MasterParty = apps.get_model('billing', 'MasterParty')
    InvoiceLine = apps.get_model('billing', 'InvoiceLine')
    load_seed(MasterParty, InvoiceLine, only_if_empty=True)


def unload(apps, schema_editor):
    # ロールバック時は取込明細(ledger)のみ削除。マスタ・手入力分は保持。
    apps.get_model('billing', 'InvoiceLine').objects.filter(source='ledger').delete()


class Migration(migrations.Migration):
    dependencies = [('billing', '0001_initial')]
    operations = [migrations.RunPython(load, unload)]
