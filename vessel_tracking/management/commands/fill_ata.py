"""Datalastic の AIS 履歴から、過去出荷の ATA(入港実績) を埋める管理コマンド。

仕組み:
  本船名 → vessel_find で uuid 解決 → vessel_history で ETA 前後の航跡を取得 →
  仕向地(東京/大阪)の港エリアに進入して停船した最初の時刻を ATA(JST日付) とする。

確証が持てない便(本船名なし/船名解決不可/港で停船を検出できない)は空欄のまま残す
(推測で日付を入れない)。既に ATA が入っている便は上書きしない。

使い方:
  python manage.py fill_ata --dry-run            # 取得して結果だけ表示(DB変更なし)
  python manage.py fill_ata                       # 実行(ATA を保存)
  python manage.py fill_ata --limit 5             # 先頭5件だけ
  python manage.py fill_ata --pk 120              # 特定の1件だけ
  python manage.py fill_ata --max-credits 200     # 消費クレジット上限(超えたら停止)

API キーは --api-key、環境変数 DATALASTIC_API_KEY、または BASE_DIR/.env から読む。
クレジットは vessel_history が「1船×1日=1」。各便は既定で ETA-4〜+7日(約12日)分。
"""
import datetime
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from vessel_tracking.models import Shipment

BASE = 'https://api.datalastic.com/api/v0/'

# 仕向地 → 港の中心座標。port_find で取得を試み、失敗時はこの既定値。
PORT_FALLBACK = {
    'TOKYO': (35.61168, 139.8268),
    'OSAKA': (34.61406, 135.4404),
}
# 仕向地 → port_find 検索名
PORT_QUERY = {'TOKYO': 'Tokyo', 'OSAKA': 'Osaka'}
# 仕向地 → AIS destination に現れる港トークン(隣港の誤検出を防ぐ確認用)。
# 例: 東京=「JP TYO Y」、大阪=「JP OSA 3W」、神戸(別港)=「JP UKB」。
PORT_TOKEN = {'TOKYO': 'TYO', 'OSAKA': 'OSA'}


def _haversine(la1, lo1, la2, lo2):
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Command(BaseCommand):
    help = 'Datalastic の AIS 履歴から過去出荷の ATA を埋める'

    def add_arguments(self, parser):
        parser.add_argument('--api-key', default=None)
        parser.add_argument('--dry-run', action='store_true', help='DB を変更せず結果のみ表示')
        parser.add_argument('--limit', type=int, default=0, help='処理する出荷の上限(0=全件)')
        parser.add_argument('--pk', type=int, default=None, help='特定の Shipment.pk のみ')
        parser.add_argument('--before', type=int, default=4, help='ETA の何日前から取得するか')
        parser.add_argument('--after', type=int, default=7, help='ETA の何日後まで取得するか')
        parser.add_argument('--radius', type=float, default=20.0,
                            help='港中心からの判定半径(km)。AIS仕向コードが一致する停船はこの範囲で採用')
        parser.add_argument('--tight', type=float, default=12.0,
                            help='AIS仕向コードが一致しない場合に採用する近距離半径(km)。隣港の誤検出防止')
        parser.add_argument('--max-credits', type=int, default=0,
                            help='vessel_history の消費クレジット上限(0=無制限)')
        parser.add_argument('--sleep', type=float, default=0.2, help='API 呼び出し間隔(秒)')

    # ---- API ----
    def _api(self, ep, **params):
        params['api-key'] = self.key
        url = BASE + ep + '?' + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:200]
                if e.code == 429 and attempt < 2:   # レート制限 → 待って再試行
                    time.sleep(2 + attempt * 2)
                    continue
                raise CommandError(f'{ep} HTTP {e.code}: {body}')
            except Exception as e:   # noqa: BLE001
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise CommandError(f'{ep} 失敗: {e}')
        return None

    def _read_key(self, arg_key):
        if arg_key:
            return arg_key
        env = os.environ.get('DATALASTIC_API_KEY')
        if env:
            return env
        env_path = os.path.join(settings.BASE_DIR, '.env')
        if os.path.exists(env_path):
            with open(env_path, encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith('DATALASTIC_API_KEY='):
                        return line.strip().split('=', 1)[1].strip().strip('"').strip("'")
        raise CommandError('API キーがありません(--api-key / DATALASTIC_API_KEY / .env)。')

    def _port_center(self, dest):
        if dest in self._port_cache:
            return self._port_cache[dest]
        center = PORT_FALLBACK.get(dest)
        q = PORT_QUERY.get(dest)
        if q:
            try:
                d = self._api('port_find', name=q)
                jp = [p for p in (d.get('data') or []) if p.get('country_iso') == 'JP']
                if jp and jp[0].get('lat') and jp[0].get('lon'):
                    center = (float(jp[0]['lat']), float(jp[0]['lon']))
            except CommandError:
                pass
        self._port_cache[dest] = center
        return center

    def _resolve_uuid(self, name):
        if name in self._uuid_cache:
            return self._uuid_cache[name]
        result = ('', 'not_found')
        try:
            d = self._api('vessel_find', name=name)
            vessels = d.get('data') or []
            containers = [v for v in vessels if (v.get('type_specific') or '').lower().startswith('container')]
            cand = containers or vessels
            if len(cand) == 1:
                result = (cand[0]['uuid'], 'ok')
            elif len(cand) > 1:
                result = ('', 'ambiguous')
        except CommandError:
            result = ('', 'find_error')
        self._uuid_cache[name] = result
        return result

    def _arrival(self, uuid, eta, dest, before, after, radius, tight):
        """ETA 前後の航跡から、仕向港での初停船時刻(UTC datetime)を返す。無ければ None。

        誤検出(隣港)対策: 停船点のうち
          1. AIS仕向コードが仕向地と一致するもの(半径 radius 内)を最優先、
          2. 無ければ港中心から tight km 以内の停船、
        の順で最初の時刻を採用する。どちらも無ければ None(=推測しない)。
        """
        frm = (eta - datetime.timedelta(days=before)).isoformat()
        to = (eta + datetime.timedelta(days=after)).isoformat()
        ck = (uuid, frm, to)
        if ck in self._hist_cache:
            d = self._hist_cache[ck]
        else:
            d = self._api('vessel_history', uuid=uuid, **{'from': frm, 'to': to})
            self._hist_cache[ck] = d
            self.credits += before + after
            time.sleep(self._sleep)
        plat, plon = self._port_center(dest)
        token = PORT_TOKEN.get(dest, '')
        pos = (d.get('data') or {}).get('positions') or []
        pos.sort(key=lambda x: x['last_position_epoch'])
        stopped = [(p, _haversine(p['lat'], p['lon'], plat, plon)) for p in pos
                   if (p.get('speed') or 0) < 1.5
                   and _haversine(p['lat'], p['lon'], plat, plon) <= radius]
        # 1) AIS仕向コード一致を優先
        matched = [p for p, _dist in stopped if token and token in (p.get('destination') or '').upper()]
        chosen = matched[0] if matched else None
        # 2) 一致なし → 近距離(tight)内の停船を採用
        if chosen is None:
            near = [p for p, dist in stopped if dist <= tight]
            chosen = near[0] if near else None
        if chosen is None:
            return None, None
        ut = datetime.datetime.fromtimestamp(chosen['last_position_epoch'], datetime.timezone.utc)
        return ut, chosen

    def handle(self, *args, **opt):
        self.key = self._read_key(opt['api_key'])
        self._sleep = opt['sleep']
        self._port_cache = {}
        self._uuid_cache = {}
        self._hist_cache = {}
        self.credits = 0
        radius = opt['radius']
        maxc = opt['max_credits']

        qs = Shipment.objects.filter(is_cancelled=False, ata__isnull=True).exclude(vessel='')
        qs = qs.exclude(eta__isnull=True).filter(dest__in=PORT_FALLBACK.keys())
        if opt['pk']:
            qs = qs.filter(pk=opt['pk'])
        qs = qs.order_by('-eta')
        if opt['limit']:
            qs = qs[:opt['limit']]

        rows = list(qs)
        self.stdout.write(f'対象: {len(rows)} 件(本船名あり・ATA未入力・仕向地TOKYO/OSAKA)')
        filled = no_arrival = skipped = 0

        for s in rows:
            if maxc and self.credits >= maxc:
                self.stdout.write(self.style.WARNING(f'クレジット上限 {maxc} 到達。残りは未処理で停止。'))
                break
            uuid, st = self._resolve_uuid(s.vessel)
            if st != 'ok':
                skipped += 1
                self.stdout.write(f'  SKIP  #{s.pk} {s.vessel}/{s.voyage} {s.dest} ETA{s.eta} — 船名解決:{st}')
                continue
            ut, pt = self._arrival(uuid, s.eta, s.dest, opt['before'], opt['after'],
                                   radius, opt['tight'])
            if not ut:
                no_arrival += 1
                self.stdout.write(f'  NONE  #{s.pk} {s.vessel}/{s.voyage} {s.dest} ETA{s.eta} — 港で停船を検出できず')
                continue
            jst = ut + datetime.timedelta(hours=9)
            ata = jst.date()
            dist = _haversine(pt['lat'], pt['lon'], *self._port_center(s.dest))
            delay = (ata - s.eta).days
            mark = '' if opt['dry_run'] else '✓'
            self.stdout.write(self.style.SUCCESS(
                f'  ATA{mark} #{s.pk} {s.vessel}/{s.voyage} {s.dest} ETA{s.eta} '
                f'-> ATA {ata}(予定比{delay:+d}日) [JST {jst:%m-%d %H:%M} {dist:.1f}km dest={pt.get("destination")}]'))
            if not opt['dry_run']:
                s.ata = ata
                s.save(update_fields=['ata', 'updated_at'])
            filled += 1

        self.stdout.write('')
        head = '【ドライラン】' if opt['dry_run'] else '【実行】'
        self.stdout.write(self.style.SUCCESS(
            f'{head} ATA特定 {filled} / 停船未検出 {no_arrival} / 船名解決不可 {skipped} '
            f'/ 消費クレジット約 {self.credits}'))
