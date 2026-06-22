"""本船名「MATLAS」を正しい「M.ATLAS」に是正する一回限りのデータ修正。

簡易入力フォームの本船名マスクが英字とスペース以外を除去していたため、入力された
「M.ATLAS」のピリオドが落ちて「MATLAS」で保存され、AIS(Datalastic)で船名解決できず
ライブ追跡できなかった。マスクは . - 数字を許可するよう修正済み(再発防止)。
ここでは既存レコードを正しい船名に直す。
"""
from django.db import migrations


def fix_matlas(apps, schema_editor):
    Shipment = apps.get_model('vessel_tracking', 'Shipment')
    Shipment.objects.filter(vessel='MATLAS', voyage='2619N').update(vessel='M.ATLAS')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('vessel_tracking', '0010_fix_bogus_ata'),
    ]

    operations = [
        migrations.RunPython(fix_matlas, noop),
    ]
