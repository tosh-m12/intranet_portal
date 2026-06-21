"""同名異航海の現在停泊による ATA 誤充填データを是正する一回限りのデータ修正。

背景: track_vessels の ATA 自動記入は船名一致のみで別航海ガードが無かったため、
旧便(ATA未記入)が、同名の本船が後日また着地港へ着岸した時刻で誤って ATA を埋められ、
入港遅延が +200 日超などになっていた。コード側は別航海ガードを追加済み(再発防止)。
ここでは既存の誤データを是正する。
"""
from datetime import date

from django.db import migrations


def fix_bogus_ata(apps, schema_editor):
    Shipment = apps.get_model('vessel_tracking', 'Shipment')
    # 1) 明らかに不正な ATA をクリア。着地は上海から通常数日で、出港(ATD)から45日超の入港は
    #    物理的にあり得ない → 同名異航海の現在停泊による誤充填と断定し None に戻す。
    for s in Shipment.objects.filter(ata__isnull=False, atd__isnull=False):
        if (s.ata - s.atd).days > 45:
            s.ata = None
            s.save(update_fields=['ata'])
    # 2) 報告のあった SNL NANJING / 2542E は AIS実績(2025-10-19 東京着岸)で正しく復元する。
    Shipment.objects.filter(vessel='SNL NANJING', voyage='2542E', dest='TOKYO').update(
        ata=date(2025, 10, 19))


def noop(apps, schema_editor):
    # データ是正のため逆操作なし(誤データには戻さない)。
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('vessel_tracking', '0009_shipper_text'),
    ]

    operations = [
        migrations.RunPython(fix_bogus_ata, noop),
    ]
