"""売上推移ダッシュボードの参照データ。

財務部の売上(収入ベース)を `sales.db` から取り込んだ読み取り専用ビュー。
顧客の分類(系列)と月別売上を保持し、集計はビュー側で動的に行う。
データは seed.json 経由(build_seed コマンドで生成 → migration で投入)。
"""
from django.db import models
from django.utils.translation import gettext_lazy as _


class Customer(models.Model):
    """売上の集計単位。系列(klass)は積み上げグラフの区分に対応。"""

    KLASS_FAURECIA = 'faurecia'
    KLASS_MAJOR = 'major'
    KLASS_OTHER = 'other'
    KLASS_GREY = 'grey'
    KLASS_CHOICES = [
        (KLASS_FAURECIA, 'Faurecia'),
        (KLASS_MAJOR, '大口顧客'),
        (KLASS_OTHER, 'その他'),
        (KLASS_GREY, 'グレー(調整・予估・外包)'),
    ]

    code = models.CharField(_('顧客コード'), max_length=16, unique=True)
    group_name = models.CharField(_('グループ名'), max_length=128, blank=True)
    customer_name = models.CharField(_('顧客名'), max_length=255, blank=True)
    klass = models.CharField(_('系列'), max_length=16, choices=KLASS_CHOICES)
    # その他の開始年バケット。22年以前は 2022 に丸める。その他以外は null。
    other_start_year = models.IntegerField(_('その他開始年'), null=True, blank=True)

    class Meta:
        verbose_name = _('顧客')
        verbose_name_plural = _('顧客')
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.group_name}'


class MonthlySale(models.Model):
    """顧客×年月の売上(円, 収入ベース)。調整でマイナスもあり得る。"""

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='sales')
    year = models.IntegerField(_('年'))
    month = models.IntegerField(_('月'))
    amount = models.FloatField(_('売上'))

    class Meta:
        verbose_name = _('月別売上')
        verbose_name_plural = _('月別売上')
        indexes = [models.Index(fields=['year', 'month'])]

    def __str__(self):
        return f'{self.customer.code} {self.year}/{self.month:02d} {self.amount:,.0f}'
