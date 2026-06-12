"""(旧)初期データ投入。

当初はここで seed を投入していたが、後続 migration(0003〜0007)で追加した
フィールドを含む seed を、フィールド未追加のこの時点で読み込むと migrate が失敗する。
そのため実投入は全フィールドが揃う 0008_load_seed へ移動し、本 migration は no-op とする。
(既存環境では本 migration は適用済みのため、内容変更の影響はない)
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('vessel_tracking', '0001_initial')]
    operations = []
