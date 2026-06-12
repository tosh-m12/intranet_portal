"""荷主を vessel 独自マスタ(Customer FK)から、billing 取引先マスタ参照のテキストへ変更。

既存の Shipment.customer(FK) の名称を新テキスト欄へ移し、Customer モデルを削除する。
"""
from django.db import migrations, models


def copy_names(apps, schema_editor):
    Shipment = apps.get_model('vessel_tracking', 'Shipment')
    for s in Shipment.objects.all():
        s.customer_txt = (s.customer.name if s.customer_id else '')
        s.save(update_fields=['customer_txt'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('vessel_tracking', '0008_load_seed')]

    operations = [
        migrations.AddField(
            'shipment', 'customer_txt',
            models.CharField(default='', max_length=128, blank=True),
        ),
        migrations.RunPython(copy_names, noop),
        migrations.RemoveField('shipment', 'customer'),
        migrations.DeleteModel('Customer'),
        migrations.RenameField('shipment', 'customer_txt', 'customer'),
        migrations.AlterField(
            'shipment', 'customer',
            models.CharField(blank=True, max_length=128, verbose_name='荷主'),
        ),
    ]
