# 課題の論理削除フラグ名を実態に合わせて変更:
#   is_cancelled -> is_hidden / cancelled_at -> hidden_at
# 「中止」ではなく、責任者が完了案件を確認して一覧から消す「非表示」操作を表す。
# RenameField で列データ（既存の非表示状態）を保持したまま改名する。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cs_tasks', '0012_weeklyreportconfig_source_lang_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='task',
            old_name='is_cancelled',
            new_name='is_hidden',
        ),
        migrations.RenameField(
            model_name='task',
            old_name='cancelled_at',
            new_name='hidden_at',
        ),
        migrations.AlterField(
            model_name='task',
            name='is_hidden',
            field=models.BooleanField(default=False, verbose_name='非表示'),
        ),
        migrations.AlterField(
            model_name='task',
            name='hidden_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='非表示日時'),
        ),
    ]
