"""本船動向管理 データモデル。

- Customer … 荷主マスタ(社名)。出荷トレーシングの「正」。将来の客先通知に備えた基準。
- Shipment … 出荷1件(=トレーシング表の1行)。受注→ブッキング確定→出港(ATD)→
              入港(ATA) のライフサイクルを担当者が手入力で追跡する。
              1注文(order_no)が仕向地別(大阪/東京)に複数の Shipment へ分かれる。

ポータル規約: authsys.User を FK 参照(入力者=監査)、物理削除せず is_cancelled で論理削除。
将来: 本船動向 API 連携・客先メール通知(ブッキング確定/遅延/出航)を追加予定(現時点は未実装)。
"""
import datetime
from urllib.parse import quote

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

# 輸送区分(LCL/FCL)の代表値。自由入力だが入力候補として提示する。
CONTAINER_TYPES = ['LCL', '20F', '40F', '40HQ', '45HQ']


class Customer(models.Model):
    """荷主マスタ。出荷トレーシングの対象荷主(取引先)。

    code は半角英数大文字の短縮コード(例: ASAHI)。name は正式社名(例: 朝日電器)。
    出荷入力時の荷主はこのマスタからのみ選択でき、自由入力はできない。
    将来の客先通知で連絡先(子テーブル)をぶら下げる起点になる。
    """
    code = models.CharField(_('荷主コード'), max_length=16, unique=True)
    name = models.CharField(_('荷主名'), max_length=255)
    is_active = models.BooleanField(_('有効'), default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']
        verbose_name = _('荷主マスタ')
        verbose_name_plural = _('荷主マスタ')

    def __str__(self):
        return f'{self.code} / {self.name}'


class Shipment(models.Model):
    """出荷1件分のトレーシング。1レコード=トレーシング表の1行。"""

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT,
                                 related_name='shipments', verbose_name=_('荷主'))

    # ---- 受注時に判明する情報 ----
    order_no = models.CharField(_('注文No'), max_length=32, blank=True)
    order_received = models.DateField(_('受注日'), null=True, blank=True)
    inv_no = models.CharField(_('インボイスNo'), max_length=64, blank=True)
    origin = models.CharField(_('仕出地'), max_length=32, blank=True)   # 出荷元(例: SHANGHAI)
    dest = models.CharField(_('仕向地'), max_length=32, blank=True)     # 届け先(例: TOKYO)

    # ---- 貨物量(貨物確定時) ----
    out_ctn = models.IntegerField(_('カートン数'), null=True, blank=True)
    out_m3 = models.FloatField(_('容積(M³)'), null=True, blank=True)
    out_qty = models.IntegerField(_('数量'), null=True, blank=True)
    container_type = models.CharField(_('輸送区分'), max_length=16, blank=True)
    container_count = models.IntegerField(_('コンテナ本数'), null=True, blank=True)  # FCL時の本数(LCLは無し)

    # ---- ブッキング確定時に判明する情報 ----
    vessel = models.CharField(_('本船名'), max_length=96, blank=True)
    voyage = models.CharField(_('航海No'), max_length=48, blank=True)
    etd = models.DateField(_('出港予定 ETD'), null=True, blank=True)
    cy_cut = models.DateField(_('CY搬入'), null=True, blank=True)
    cy_open = models.DateField(_('ECYオープン'), null=True, blank=True)       # 予定 ECYOpen
    cy_open_act = models.DateField(_('ACYオープン'), null=True, blank=True)   # 実績 ACYOpen
    vanning = models.DateField(_('バンニング'), null=True, blank=True)
    eta = models.DateField(_('入港予定 ETA'), null=True, blank=True)

    # ---- 出港後に船会社へ確認して手入力する実績 ----
    atd = models.DateField(_('出港実績 ATD'), null=True, blank=True)
    ata = models.DateField(_('入港実績 ATA'), null=True, blank=True)

    # 積地(上海)への本船入港。予定(ETA)は船社スケジュール由来の手入力、
    # 実績(ATA)は AIS から自動取得。差(実績−予定)で積地での遅延を把握する。
    shanghai_eta = models.DateField(_('上海入港予定'), null=True, blank=True)
    shanghai_ata = models.DateField(_('上海入港'), null=True, blank=True)

    remarks = models.TextField(_('備考'), blank=True)

    # ---- ライブ本船動向(vessel_pro スナップショット。track_vessels コマンドが更新) ----
    live_lat = models.FloatField(null=True, blank=True)
    live_lon = models.FloatField(null=True, blank=True)
    live_speed = models.FloatField(null=True, blank=True)        # ノット
    live_dest_unlocode = models.CharField(max_length=16, blank=True)  # 申告仕向(例 JPTYO/CNSHG)
    live_dest_name = models.CharField(max_length=64, blank=True)
    live_eta = models.DateTimeField(null=True, blank=True)       # 申告ETA(UTC, aware)
    live_nav_status = models.CharField(max_length=48, blank=True)
    live_updated_at = models.DateTimeField(null=True, blank=True)  # 取得時刻

    # 担当者は請求管理と同様、入力時にログインユーザーから自動設定する。
    assignee = models.CharField(_('担当者'), max_length=64, blank=True)

    # 取込元(ledger=既存トレーシング表取込 / manual=画面入力)
    source = models.CharField(max_length=16, default='manual')
    # 監査・論理削除(ポータル規約)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='shipments',
                                   verbose_name=_('入力者'))
    is_cancelled = models.BooleanField(_('取消'), default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # 出港予定の新しい順(=直近・今後の便を上に)。日付未定は末尾。
        ordering = ['-etd', '-id']
        verbose_name = _('出荷トレーシング')
        verbose_name_plural = _('出荷トレーシング')

    def __str__(self):
        return f'{self.order_no} {self.inv_no} → {self.dest}'

    # ---- 状況(入力済みの日付・本船から自動判定) ----
    @property
    def status(self):
        """('key', '表示名') を返す。受注 → ブッキング確定 → 出港済 → 入港済。"""
        if self.is_cancelled:
            return ('cancelled', _('取消'))
        if self.ata:
            return ('arrived', _('入港済'))
        if self.atd:
            return ('departed', _('出港済'))
        if (self.vessel or self.voyage or self.etd or self.cy_cut or self.cy_open
                or self.vanning or self.eta):
            return ('booked', _('ブッキング確定'))
        return ('received', _('受注'))

    @property
    def status_key(self):
        return self.status[0]

    @property
    def status_label(self):
        return self.status[1]

    # ---- 遅延(予定 vs 実績。API 無しでも手入力実績から算出可能) ----
    @property
    def departure_delay_days(self):
        """出港遅延日数(ATD − ETD)。正=遅延、負=前倒し。両方揃わなければ None。"""
        if self.atd and self.etd:
            return (self.atd - self.etd).days
        return None

    @property
    def arrival_delay_days(self):
        """入港遅延日数(ATA − ETA)。"""
        if self.ata and self.eta:
            return (self.ata - self.eta).days
        return None

    @property
    def shanghai_delay_days(self):
        """上海入港の予定比(実績 − 予定)。両方揃わなければ None。正=予定より遅い。"""
        if self.shanghai_ata and self.shanghai_eta:
            return (self.shanghai_ata - self.shanghai_eta).days
        return None

    @property
    def is_overdue(self):
        """出港済・未入港で ETA を過ぎている(=入港予定超過。要確認)。

        実績(ATA)を記録する運用が定着して初めて意味を持つ指標。現状は遅延判定には
        使わず(過去取込分は ATA 未記録のため)、将来の本船動向監視で利用する。
        """
        if self.atd and not self.ata and self.eta:
            return self.eta < datetime.date.today()
        return False

    @property
    def is_delayed(self):
        """実績が予定より遅れている(ATD>ETD もしくは ATA>ETA)。

        実際に記録された日付の差のみで判定する。ATA 未記録を遅延扱いしない。
        """
        if (self.departure_delay_days or 0) > 0:
            return True
        if (self.arrival_delay_days or 0) > 0:
            return True
        return False

    @property
    def delay_label(self):
        """一覧表示用の遅延ラベル(短い日本語)。遅延なしは空文字。"""
        dd = self.departure_delay_days
        ad = self.arrival_delay_days
        if dd and dd > 0:
            return _('出港+%(d)d日') % {'d': dd}
        if ad and ad > 0:
            return _('入港+%(d)d日') % {'d': ad}
        return ''

    # ---- ライブ監視(vessel_pro スナップショットからの算出) ----
    # 仕向地 → 着地港の UN/LOCODE。申告仕向がこれと一致する間だけ「日本へ航行中」とみなす。
    TARGET_UNLOCODE = {'TOKYO': 'JPTYO', 'OSAKA': 'JPOSA'}

    @property
    def target_unlocode(self):
        return self.TARGET_UNLOCODE.get(self.dest, '')

    @staticmethod
    def _to_jst(dt):
        return dt.astimezone(datetime.timezone(datetime.timedelta(hours=9))) if dt else None

    @property
    def live_eta_jst_str(self):
        d = self._to_jst(self.live_eta)
        return d.strftime('%m/%d %H:%M') if d else ''

    @property
    def live_updated_jst_str(self):
        d = self._to_jst(self.live_updated_at)
        return d.strftime('%m/%d %H:%M') if d else ''

    @property
    def live_phase(self):
        """ライブスナップショットの局面キー。"""
        if not self.live_updated_at:
            return 'none'
        if self.ata:
            return 'arrived'
        u = (self.live_dest_unlocode or '').upper()
        if self.target_unlocode and u == self.target_unlocode:
            return 'to_dest'       # 着地(日本)へ航行中 → 申告ETAが使える
        if u == 'CNSHG' and not self.atd:
            return 'to_origin'     # 上海へ向け航行中(出港前の先行指標)
        return 'other'            # 別レグ(無関係)

    @property
    def live_predicted_delay_days(self):
        """日本へ航行中の時、申告ETA(着地) − 予定ETA の日数。それ以外は None。"""
        if self.live_phase == 'to_dest' and self.live_eta and self.eta:
            return (self._to_jst(self.live_eta).date() - self.eta).days
        return None

    @property
    def live_alert(self):
        """(レベル, ラベル)。レベル: bad/ok/info/muted/none。"""
        p = self.live_phase
        if p == 'to_dest':
            d = self.live_predicted_delay_days
            if d is None:
                return ('info', _('日本へ航行中'))
            if d > 0:
                return ('bad', _('遅延予測 +%(d)d日') % {'d': d})
            return ('ok', _('順調'))
        if p == 'to_origin':
            return ('info', _('上海へ航行中'))
        if p == 'arrived':
            return ('ok', _('入港済'))
        if p == 'other':
            return ('muted', _('別レグ'))
        return ('none', _('未取得'))

    @property
    def live_departure_predict(self):
        """発地(上海)出発の予測/実績アラート (level, label)。

        - 既に出港(atd)済み → 実績 ATD−ETD。
        - 未出港で割当本船が上海へ向け航行中(live仕向=CNSHG) →
          上海到着見込み(申告ETA) を出発見込みとみなし、予定ETD と比較。
          (上海の在港は半日〜1日のため、到着≒出発)
        - それ以外(本船が別レグ等) → 監視中。
        """
        # 出港済 → 実績の出発遅延
        if self.atd and self.etd:
            d = (self.atd - self.etd).days
            return ('bad', _('出発済 +%(d)d日') % {'d': d}) if d > 0 else ('ok', _('出発済(予定通り)'))
        if self.atd:
            return ('ok', _('出発済'))
        # 未出港で予定ETDを過ぎている → 既に遅延発生(確定。ライブ値が無くても警告)
        if self.etd and self.etd < datetime.date.today():
            d = (datetime.date.today() - self.etd).days
            return ('bad', _('出発遅延 +%(d)d日(ETD経過・未出港)') % {'d': d})
        # 未出港・ETD前 → 割当本船の上海到着見込みで予測
        if (self.live_updated_at and (self.live_dest_unlocode or '').upper() == 'CNSHG'
                and self.live_eta and self.etd):
            arr = self._to_jst(self.live_eta).date()
            d = (arr - self.etd).days
            return ('bad', _('出発遅延予測 +%(d)d日') % {'d': d}) if d > 0 else ('ok', _('出発予定通り'))
        if self.live_updated_at:
            return ('muted', _('監視中'))
        return ('none', _('未取得'))

    @property
    def live_map_url(self):
        """現在地を中国で使える地図(高德AMap)で開くURL。WGS-84 を自動変換(coordinate=wgs84)。"""
        if self.live_lat is None or self.live_lon is None:
            return ''
        name = quote(f'{self.vessel} {self.voyage}'.strip() or '本船')
        return ('https://uri.amap.com/marker?position=%s,%s&name=%s&coordinate=wgs84&callnative=0'
                % (self.live_lon, self.live_lat, name))

    @property
    def live_pos_str(self):
        if self.live_lat is None or self.live_lon is None:
            return ''
        return f'{self.live_lat:.2f}, {self.live_lon:.2f}'

    @property
    def origin_eta(self):
        """発地(上海)到着予定。未出港はライブ監視の上海行きETAを優先、無ければ手入力値。"""
        if not self.atd and (self.live_dest_unlocode or '').upper() == 'CNSHG' and self.live_eta:
            return self._to_jst(self.live_eta).date()
        return self.shanghai_eta

    @property
    def live_departure_delay_days(self):
        """未出港便の出発遅延(予想)日数。出港済み/予測なしは None。

        ライブの上海到着見込み(=出発見込み)が予定ETDより後、または予定ETDを既に過ぎている場合に
        その日数を返す(両者の大きい方)。
        """
        if self.atd or not self.etd:
            return None
        days = 0
        if (self.live_updated_at and (self.live_dest_unlocode or '').upper() == 'CNSHG'
                and self.live_eta):
            days = max(days, (self._to_jst(self.live_eta).date() - self.etd).days)
        if self.etd < datetime.date.today():
            days = max(days, (datetime.date.today() - self.etd).days)
        return days if days > 0 else None

    @property
    def shanghai_eta_live_jst_str(self):
        """ライブ仕向が上海の時の到着見込み(=出発見込み)。それ以外は空。"""
        if (self.live_dest_unlocode or '').upper() == 'CNSHG':
            return self.live_eta_jst_str
        return ''

    def save(self, *args, **kwargs):
        self.origin = (self.origin or '').strip().upper()
        self.dest = (self.dest or '').strip().upper()
        self.container_type = (self.container_type or '').strip().upper()
        super().save(*args, **kwargs)
