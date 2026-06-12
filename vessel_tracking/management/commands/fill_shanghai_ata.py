"""Datalastic の AIS 履歴から、本船の「上海入港」(積地インバウンドATA)を埋める。

仕組み:
  本船名 → vessel_find で uuid 解決 → 出港(ATD)前後の航跡を取得 →
  上海の積地ターミナル(外高橋/洋山)エリアへ進入してバース停泊した区間の
  「港域進入時刻」を上海入港(JST日付)とする。

確証が持てない便(本船名なし/解決不可/上海寄港を検出できず)は空欄のまま。
既に値がある便は上書きしない。

  python manage.py fill_shanghai_ata --dry-run
  python manage.py fill_shanghai_ata
  python manage.py fill_shanghai_ata --limit 10 --pk 120 --max-credits 400
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
# 上海 積地ターミナル(外高橋 / 洋山)。どちらかの半径内なら上海港とみなす。
SHANGHAI_CENTERS = [(31.34, 121.62), (30.62, 122.07)]


def _haversine(la1, lo1, la2, lo2):
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Command(BaseCommand):
    help = 'Datalastic の AIS 履歴から本船の上海入港(インバウンドATA)を埋める'

    def add_arguments(self, parser):
        parser.add_argument('--api-key', default=None)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--pk', type=int, default=None)
        parser.add_argument('--before', type=int, default=4, help='基準日の何日前から取得')
        parser.add_argument('--after', type=int, default=1, help='基準日の何日後まで取得')
        parser.add_argument('--radius', type=float, default=25.0, help='上海港の判定半径(km)')
        parser.add_argument('--max-credits', type=int, default=0)
        parser.add_argument('--sleep', type=float, default=0.2)

    def _api(self, ep, **params):
        params['api-key'] = self.key
        url = BASE + ep + '?' + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:200]
                if e.code == 429 and attempt < 2:
                    time.sleep(2 + attempt * 2)
                    continue
                raise CommandError(f'{ep} HTTP {e.code}: {body}')
            except Exception as e:  # noqa: BLE001
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise CommandError(f'{ep} 失敗: {e}')
        return None

    def _read_key(self, arg_key):
        if arg_key:
            return arg_key
        if os.environ.get('DATALASTIC_API_KEY'):
            return os.environ['DATALASTIC_API_KEY']
        env_path = os.path.join(settings.BASE_DIR, '.env')
        if os.path.exists(env_path):
            with open(env_path, encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith('DATALASTIC_API_KEY='):
                        return line.strip().split('=', 1)[1].strip().strip('"').strip("'")
        raise CommandError('API キーがありません(--api-key / DATALASTIC_API_KEY / .env)。')

    def _resolve_uuid(self, name):
        if name in self._uuid_cache:
            return self._uuid_cache[name]
        try:
            vessels = self._api('vessel_find', name=name).get('data') or []
        except CommandError:
            # 一時的な通信失敗はキャッシュしない(後続/再実行で取り直す)
            return ('', 'find_error')
        cand = [v for v in vessels
                if (v.get('type_specific') or '').lower().startswith('container')] or vessels
        if len(cand) == 1:
            result = (cand[0]['uuid'], 'ok')
        elif len(cand) > 1:
            result = ('', 'ambiguous')
        else:
            result = ('', 'not_found')
        self._uuid_cache[name] = result
        return result

    @staticmethod
    def _dist_shanghai(p):
        return min(_haversine(p['lat'], p['lon'], c[0], c[1]) for c in SHANGHAI_CENTERS)

    def _arrival(self, uuid, anchor, before, after, radius):
        """anchor(=出港予定/実績) 前後の航跡から、上海港への進入(=入港)時刻 UTC を返す。"""
        frm = (anchor - datetime.timedelta(days=before)).isoformat()
        to = (anchor + datetime.timedelta(days=after)).isoformat()
        ck = (uuid, frm, to)
        if ck in self._hist_cache:
            d = self._hist_cache[ck]
        else:
            d = self._api('vessel_history', uuid=uuid, **{'from': frm, 'to': to})
            self._hist_cache[ck] = d
            self.credits += before + after
            time.sleep(self._sleep)
        pos = (d.get('data') or {}).get('positions') or []
        pos.sort(key=lambda x: x['last_position_epoch'])

        def inport(p):
            return self._dist_shanghai(p) <= radius
        # 出港直前の上海バース停泊(speed<1.5)を探し、その停泊区間の港域進入点=入港。
        berth = [i for i, p in enumerate(pos) if inport(p) and (p.get('speed') or 0) < 1.5]
        if not berth:
            return None, None
        last = max(berth)
        j = last
        while j > 0 and inport(pos[j - 1]):
            j -= 1
        arr = pos[j]
        ut = datetime.datetime.fromtimestamp(arr['last_position_epoch'], datetime.timezone.utc)
        return ut, arr

    def handle(self, *args, **opt):
        self.key = self._read_key(opt['api_key'])
        self._sleep = opt['sleep']
        self._uuid_cache = {}
        self._hist_cache = {}
        self.credits = 0
        radius = opt['radius']
        maxc = opt['max_credits']

        qs = Shipment.objects.filter(is_cancelled=False, shanghai_ata__isnull=True).exclude(vessel='')
        if opt['pk']:
            qs = qs.filter(pk=opt['pk'])
        qs = qs.order_by('-etd')
        if opt['limit']:
            qs = qs[:opt['limit']]
        rows = list(qs)
        self.stdout.write(f'対象: {len(rows)} 件(本船名あり・上海入港未取得)')
        filled = none = skipped = 0

        for s in rows:
            if maxc and self.credits >= maxc:
                self.stdout.write(self.style.WARNING(f'クレジット上限 {maxc} 到達。残りは未処理。'))
                break
            anchor = s.atd or s.etd
            if not anchor:
                skipped += 1
                self.stdout.write(f'  SKIP #{s.pk} {s.vessel} — 基準日(ATD/ETD)なし')
                continue
            uuid, st = self._resolve_uuid(s.vessel)
            if st != 'ok':
                skipped += 1
                self.stdout.write(f'  SKIP #{s.pk} {s.vessel}/{s.voyage} — 船名解決:{st}')
                continue
            ut, pt = self._arrival(uuid, anchor, opt['before'], opt['after'], radius)
            if not ut:
                none += 1
                self.stdout.write(f'  NONE #{s.pk} {s.vessel}/{s.voyage} 基準{anchor} — 上海寄港を検出できず')
                continue
            jst = ut + datetime.timedelta(hours=9)
            arr = jst.date()
            mark = '' if opt['dry_run'] else '✓'
            self.stdout.write(self.style.SUCCESS(
                f'  上海入港{mark} #{s.pk} {s.vessel}/{s.voyage} 基準{anchor} -> {arr} '
                f'[JST {jst:%m-%d %H:%M} {self._dist_shanghai(pt):.1f}km]'))
            if not opt['dry_run']:
                s.shanghai_ata = arr
                s.save(update_fields=['shanghai_ata', 'updated_at'])
            filled += 1

        self.stdout.write('')
        head = '【ドライラン】' if opt['dry_run'] else '【実行】'
        self.stdout.write(self.style.SUCCESS(
            f'{head} 上海入港 特定 {filled} / 未検出 {none} / スキップ {skipped} '
            f'/ 消費クレジット約 {self.credits}'))
