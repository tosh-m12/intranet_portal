"""承認機能の導入時、直近に登録済みのバッチを未承認に戻して管理者レビュー対象にする。

本番では連番 202606100062 まで登録済み。承認機能導入前の入力分のうち、連番
202606100040 以降を一旦「未承認」にする(それ以前の履歴は承認済みのまま)。
連番は YYYYMMDD+4桁・年内連番のため、文字列比較=時系列比較で gte 判定できる。
ローカル等で該当連番が無ければ何もしない(no-op)。
"""
from django.db import migrations

CUTOFF_SERIAL = '202606100040'


def mark_unapproved(apps, schema_editor):
    InvoiceLine = apps.get_model('billing', 'InvoiceLine')
    InvoiceLine.objects.filter(serial__gte=CUTOFF_SERIAL, is_cancelled=False).update(is_approved=False)


def revert(apps, schema_editor):
    InvoiceLine = apps.get_model('billing', 'InvoiceLine')
    InvoiceLine.objects.filter(serial__gte=CUTOFF_SERIAL, is_cancelled=False).update(is_approved=True)


class Migration(migrations.Migration):
    dependencies = [
        ('billing', '0005_invoiceline_approved_at_invoiceline_approved_by_and_more'),
    ]
    operations = [migrations.RunPython(mark_unapproved, revert)]
