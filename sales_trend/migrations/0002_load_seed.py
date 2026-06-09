"""初期データ投入: 顧客分類 + 月別売上(seed.json)。

本番でも `migrate` 実行時に自動投入される。既存データがあればスキップ(冪等)。
seed.json は開発機で `manage.py build_seed`(sales.db 由来)により再生成する。
"""
from django.db import migrations


def load(apps, schema_editor):
    from sales_trend.seedload import load_seed
    Customer = apps.get_model('sales_trend', 'Customer')
    MonthlySale = apps.get_model('sales_trend', 'MonthlySale')
    load_seed(Customer, MonthlySale, only_if_empty=True)


def unload(apps, schema_editor):
    apps.get_model('sales_trend', 'MonthlySale').objects.all().delete()
    apps.get_model('sales_trend', 'Customer').objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [('sales_trend', '0001_initial')]
    operations = [migrations.RunPython(load, unload)]
