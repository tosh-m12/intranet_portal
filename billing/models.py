"""請求書管理 データモデル。

- MasterParty … 担当者基準テーブル(グループ名/会社名/担当者)。取引先の「正」。
                正キーは (グループ名 × 会社名) のペア。
- InvoiceLine … 請求書1枚(=1明細行)。費目別の税抜額(canonical)＋税率を持ち、
                費目税込・税抜合計・税込合計・換算後金額は元Excelの数式どおり自動計算する。

ポータル規約: authsys.User を FK 参照(入力者=監査)、物理削除せず is_cancelled で論理削除。
"""
import datetime

from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

# 8 費目: (フィールド接頭辞, 表示名)。各費目に <prefix>(税抜額) と <prefix>_rate(税率%) を持つ。
FEES = [
    ('storage', _('保管料 STORAGE')),
    ('warehouse', _('庫内作業 WAREHOUSE')),
    ('customs', _('通関 CUSTOMS')),
    ('service', _('サービス SERVICE')),
    ('loc_transport', _('国内輸送 LOC_TRANSPORT')),
    ('disb', _('立替 DISB')),
    ('oth', _('その他 OTH')),
    ('tpl', _('3PL')),
]
FEE_KEYS = [k for k, _label in FEES]

# 通貨は ISO 4217 の3文字コード。RMB は使わず CNY に統一。業務主要通貨を先頭に。
CURRENCIES = ['CNY', 'USD', 'JPY', 'EUR', 'HKD', 'GBP', 'KRW', 'TWD', 'SGD', 'THB',
              'AUD', 'CAD', 'CHF', 'NZD', 'SEK', 'NOK', 'DKK', 'INR', 'VND', 'MYR',
              'IDR', 'PHP', 'AED', 'SAR', 'BRL', 'MXN', 'ZAR', 'TRY', 'RUB', 'PLN',
              'CZK', 'HUF', 'ILS']


def norm_currency(v):
    v = (v or '').strip().upper()
    return 'CNY' if v == 'RMB' else v


def _r2(v):
    """小数第2位で四捨五入(Excel ROUND 相当)。"""
    return round(v + 1e-9, 2) if v else 0.0


class MasterParty(models.Model):
    """担当者基準テーブル。取引先(グループ・会社)と担当者の正規表記マスタ。

    会社名は単独ではユニークでない(同じNXフォワーダー社名が複数の顧客グループを
    担当し、担当者もグループごとに異なる)。正のキーは (グループ名 × 会社名) のペア。
    """
    group_name = models.CharField(_('グループ名'), max_length=128)
    company_name = models.CharField(_('会社名'), max_length=255)
    # 担当者はマスタで持たず請求入力時にログインユーザーから自動設定する。
    # 代わりに取引先の業務概要を保持する。
    business_summary = models.TextField(_('業務概要'), blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['group_name', 'company_name']
        constraints = [
            models.UniqueConstraint(fields=['group_name', 'company_name'],
                                    name='uniq_billing_group_company'),
        ]
        verbose_name = _('取引先マスタ')
        verbose_name_plural = _('取引先マスタ')

    def __str__(self):
        return f'{self.group_name} / {self.company_name}'


class InvoiceLine(models.Model):
    """請求書1枚分の明細(費目内訳つき)。1レコード=1請求書。"""
    # 連番 = YYYYMMDD(登録時当日) + 4桁。日付は連番に内包。4桁は暦年単位で1/1リセット。
    serial = models.CharField(_('連番'), max_length=16, blank=True, default='')
    customer_gc = models.CharField(_('顧客グループ'), max_length=128, blank=True)
    bill_to = models.CharField(_('請求先会社名'), max_length=255, blank=True)
    bill_cat = models.CharField(_('区分'), max_length=16, blank=True)
    currency = models.CharField(_('通貨'), max_length=8, blank=True, default='CNY')
    bill_year = models.IntegerField(_('計上年'), null=True, blank=True)
    bill_month = models.IntegerField(_('計上月'), null=True, blank=True)
    assignee = models.CharField(_('担当者'), max_length=64, blank=True)

    # 費目(税抜額 + 税率%)
    storage = models.FloatField(null=True, blank=True)
    storage_rate = models.FloatField(null=True, blank=True)
    warehouse = models.FloatField(null=True, blank=True)
    warehouse_rate = models.FloatField(null=True, blank=True)
    customs = models.FloatField(null=True, blank=True)
    customs_rate = models.FloatField(null=True, blank=True)
    service = models.FloatField(null=True, blank=True)
    service_rate = models.FloatField(null=True, blank=True)
    loc_transport = models.FloatField(null=True, blank=True)
    loc_transport_rate = models.FloatField(null=True, blank=True)
    disb = models.FloatField(null=True, blank=True)
    disb_rate = models.FloatField(null=True, blank=True)
    oth = models.FloatField(null=True, blank=True)
    oth_rate = models.FloatField(null=True, blank=True)
    tpl = models.FloatField(null=True, blank=True)
    tpl_rate = models.FloatField(null=True, blank=True)

    # 合計(自動計算)
    total_before_tax = models.FloatField(_('税抜合計'), null=True, blank=True)
    total_after_tax = models.FloatField(_('税込合計'), null=True, blank=True)

    # 第2通貨(2通貨記載時に記入)
    exrate = models.FloatField(_('為替レート'), null=True, blank=True)
    rate_date = models.DateField(_('レート日付'), null=True, blank=True)
    fx_currency = models.CharField(_('通貨'), max_length=8, blank=True)
    converted_amount = models.FloatField(_('換算後金額'), null=True, blank=True)

    # 取込元(ledger=既存台帳取込 / manual=画面入力)
    source = models.CharField(max_length=16, default='manual')
    # 承認(新規の画面入力は未承認で登録され、管理者が承認する。既存・台帳取込は承認済み扱い)
    is_approved = models.BooleanField(_('承認済'), default=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='billing_approvals',
                                    verbose_name=_('承認者'))
    approved_at = models.DateTimeField(_('承認日時'), null=True, blank=True)
    # 監査・論理削除(ポータル規約)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='billing_lines',
                                   verbose_name=_('入力者'))
    is_cancelled = models.BooleanField(_('取消'), default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-serial', '-id']
        verbose_name = _('請求明細')
        verbose_name_plural = _('請求明細')

    # ---- 連番の自動採番(YYYYMMDD + 4桁。4桁は暦年単位、1/1リセット) ----
    @staticmethod
    def year_suffix(s, year):
        if s and len(s) >= 12 and s[:8].isdigit() and s[8:].isdigit() and s[:4] == year:
            return int(s[8:])
        return None

    @classmethod
    def next_serial(cls, for_date=None):
        d = for_date or datetime.date.today()
        if isinstance(d, str):
            try:
                d = datetime.date.fromisoformat(d[:10])
            except ValueError:
                d = datetime.date.today()
        day = d.strftime('%Y%m%d')
        year = d.strftime('%Y')
        nums = [n for n in (cls.year_suffix(s, year)
                for s in cls.objects.values_list('serial', flat=True)) if n is not None]
        return f'{day}{(max(nums) + 1) if nums else 1:04d}'

    # ---- 自動計算ロジック(元Excel数式準拠) ----
    def fee_incl(self, key):
        amt = getattr(self, key) or 0.0
        rate = getattr(self, f'{key}_rate') or 0.0
        return _r2(amt * (1 + rate / 100.0))

    def compute_totals(self):
        before = sum((getattr(self, k) or 0.0) for k in FEE_KEYS)
        after = sum(self.fee_incl(k) for k in FEE_KEYS)
        self.total_before_tax = _r2(before)
        self.total_after_tax = _r2(after)
        # 換算後金額(2通貨) = 税込合計 ÷ 為替レート。レート未入力なら無し。
        self.converted_amount = _r2(self.total_after_tax / self.exrate) if self.exrate else None

    @property
    def tax_amount(self):
        return _r2((self.total_after_tax or 0.0) - (self.total_before_tax or 0.0))

    def save(self, *args, **kwargs):
        self.currency = norm_currency(self.currency)
        self.fx_currency = norm_currency(self.fx_currency)
        self.compute_totals()
        if not self.serial:
            # 同時保存でも連番が重複しないよう、暦年ごとのロック行で採番を直列化する。
            # ロック取得〜採番〜INSERT を 1 トランザクションに収め、SQLite でも排他になる。
            # 番号は実データの最大+1から導出するため、台帳取込で連番が入っても自己補正される。
            d = datetime.date.today()
            with transaction.atomic():
                lock, _created = SerialLock.objects.get_or_create(year=int(d.strftime('%Y')))
                # この UPDATE で書き込みロックを取得し、後続の採番計算と INSERT を排他化する。
                SerialLock.objects.filter(pk=lock.pk).update(bump=models.F('bump') + 1)
                self.serial = self.next_serial(d)
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        return f'#{self.serial} {self.bill_to}'


class SerialLock(models.Model):
    """連番採番の直列化用ロック行(暦年単位)。同時保存での連番重複を防ぐ。

    値そのものは保持せず、行ロックの取得対象としてのみ使う(番号は実データの
    最大+1から都度導出するので、台帳取込などで連番が入っても破綻しない)。
    """
    year = models.PositiveIntegerField(unique=True)
    bump = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _('連番ロック')
        verbose_name_plural = _('連番ロック')

    def __str__(self):
        return f'{self.year}: {self.bump}'
