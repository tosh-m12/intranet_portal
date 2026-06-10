"""売上データの再ロード(月別閲覧の更新を反映)。

`sales_trend/data/seed.json` を再生成(build_seed = sales.db 由来)した後、既存の
Customer/MonthlySale を一旦全削除して seed.json から再投入する。Customer/MonthlySale は
seed 由来の参照データ(担当者入力なし)のため全入れ替えで安全。
※ build_seed は荷主グループ名を請求台帳の基準名へ寄せる(§3 MAP)ため、本再ロードで
   group_name も統一名に揃う。
"""
from django.db import migrations


def reload_seed(apps, schema_editor):
    from sales_trend.seedload import load_seed
    Customer = apps.get_model('sales_trend', 'Customer')
    MonthlySale = apps.get_model('sales_trend', 'MonthlySale')
    MonthlySale.objects.all().delete()
    Customer.objects.all().delete()
    load_seed(Customer, MonthlySale, only_if_empty=False)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('sales_trend', '0002_load_seed')]
    operations = [migrations.RunPython(reload_seed, noop)]
