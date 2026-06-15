"""Datalastic の AIS 生航跡から、積地(上海)の「着岸」「離岸」を即時検出する。

背景:
  vessel_pro の集計フィールド(atd_UTC/dep_port)は生航跡より数時間遅れて更新される。
  MarineTraffic と同程度の即時性を得るため、ここでは生の positions から
  「上海ターミナルでの停泊→離岸」を直接判定し、上海入港(shanghai_ata)と
  出港実績(atd)を埋める。atd が入ると status=出港済 となりライブ監視から外れる。

対象(クレジット節約):
  取消でない・本船名あり・atd 未記入・積地=上海・ETD が直近の窓内(既定 -14〜+5日)。
  =いま上海で積込中/接近中の便だけ vessel_history を引く(通常 数隻)。

判定:
  - 着岸 = 上海ターミナル半径内でのバース停泊区間への「港域進入時刻」。
  - 離岸 = その停泊の後、最初に動き出し(>=dep-speed)、かつ
           港域外へ出る/距離が増す/申告仕向が日本 のいずれかで「出航」と確認できた時刻。
  まだ在港(動き出していない)便は離岸=未確定として atd は入れない(着岸のみ埋める)。
  日付は既存実績(track_vessels)に合わせ JST 日付で保存する。

  python manage.py track_shanghai --dry-run
  python manage.py track_shanghai --pk 121
  python manage.py track_shanghai --max-credits 200
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
from django.utils.timezone import localdate

from vessel_tracking.models import Shipment

BASE = 'https://api.datalastic.com/api/v0/'
# 上海 積地ターミナル(外高橋 / 洋山)。どちらかの半径内なら上海港とみなす。
# 沖待ち錨地(港中心から ~160km)はこの半径外なので着岸に誤認しない。
SHANGHAI_CENTERS = [(31.34, 121.62), (30.62, 122.07)]


def _haversine(la1, lo1, la2, lo2):
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _dist_sh(p):
    return min(_haversine(p['lat'], p['lon'], c[0], c[1]) for c in SHANGHAI_CENTERS)


def _utc(p):
    return datetime.datetime.fromtimestamp(p['last_position_epoch'], datetime.timezone.utc)


class Command(BaseCommand):
    help = 'AIS 生航跡から上海の着岸(上海入港)・離岸(ATD)を即時検出して記入する'

    def add_arguments(self, parser):
        parser.add_argument('--api-key', default=None)
        parser.add_argument('--dry-run', action='store_true', help='DB を変更せず結果のみ表示')
        parser.add_argument('--pk', type=int, default=None, help='特定の Shipment.pk のみ')
        parser.add_argument('--limit', type=int, default=0, help='処理上限(0=全件)')
        parser.add_argument('--window-back', type=int, default=14, help='ETD が今日の何日前まで対象か')
        parser.add_argument('--window-lead', type=int, default=5, help='ETD が今日の何日後まで対象か')
        parser.add_argument('--before', type=int, default=4, help='ETD の何日前から航跡取得')
        parser.add_argument('--after', type=int, default=6, help='ETD の何日後まで航跡取得')
        parser.add_argument('--radius', type=float, default=20.0, help='上海港の判定半径(km)')
        parser.add_argument('--dep-speed', type=float, default=3.0, help='離岸とみなす速度(kn)')
        parser.add_argument('--max-credits', type=int, default=0, help='vessel_history 消費上限(0=無制限)')
        parser.add_argument('--sleep', type=float, default=0.2)

    # ---- API ----
    def _api(self, ep, **params):
        params['api-key'] = self.key
        url = BASE + ep + '?' + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                from vessel_tracking.datalastic_ssl import api_ssl_context
                with urllib.request.urlopen(url, timeout=60, context=api_ssl_context()) as r:
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
            return ('', 'find_error')   # 一時失敗はキャッシュしない
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

    def _history(self, uuid, etd, before, after):
        frm = (etd - datetime.timedelta(days=before)).isoformat()
        to = (etd + datetime.timedelta(days=after)).isoformat()
        ck = (uuid, frm, to)
        if ck not in self._hist_cache:
            d = self._api('vessel_history', uuid=uuid, **{'from': frm, 'to': to})
            self._hist_cache[ck] = d
            self.credits += before + after
            time.sleep(self._sleep)
        return self._hist_cache[ck]

    def _visit(self, pos, radius, dep_speed):
        """上海寄港の (arrival_utc, departure_utc, last_pos)。
        在港中(まだ離岸せず)は departure=None。寄港なしは (None, None, None)。"""
        pos = sorted(pos, key=lambda x: x['last_position_epoch'])
        inport = [_dist_sh(p) <= radius for p in pos]
        berth = [i for i, p in enumerate(pos)
                 if inport[i] and (p.get('speed') or 0) < 1.5]
        if not berth:
            return None, None, None
        last_b = max(berth)
        # 着岸: 最新停泊を含む港域連続区間の「進入点」まで遡る
        start = last_b
        while start > 0 and inport[start - 1]:
            start -= 1
        arr = pos[start]
        # 離岸: last_b 以降で最初に動き出した点。出航と確認できた時のみ採用
        dep = None
        for k in range(last_b + 1, len(pos)):
            if (pos[k].get('speed') or 0) >= dep_speed:
                later = pos[k:]
                leaves = any(_dist_sh(p) > radius for p in later)
                moving_out = (max(_dist_sh(p) for p in later) - _dist_sh(pos[k])) >= 3.0
                jp = any('JP' in (p.get('destination') or '').upper() for p in later)
                if leaves or moving_out or jp:
                    dep = pos[k]
                break
        return _utc(arr), (_utc(dep) if dep else None), pos[-1]

    def handle(self, *args, **opt):
        self.key = self._read_key(opt['api_key'])
        self._sleep = opt['sleep']
        self._uuid_cache = {}
        self._hist_cache = {}
        self.credits = 0
        radius = opt['radius']
        dep_speed = opt['dep_speed']
        maxc = opt['max_credits']
        today = localdate()

        qs = (Shipment.objects.filter(is_cancelled=False, atd__isnull=True, origin='SHANGHAI')
              .exclude(vessel='').filter(etd__isnull=False)
              .filter(etd__gte=today - datetime.timedelta(days=opt['window_back']),
                      etd__lte=today + datetime.timedelta(days=opt['window_lead'])))
        if opt['pk']:
            qs = Shipment.objects.filter(pk=opt['pk'])   # pk 指定は窓を無視
        qs = qs.order_by('-etd')
        if opt['limit']:
            qs = qs[:opt['limit']]
        rows = list(qs)
        self.stdout.write(f'対象: {len(rows)} 件(積地=上海・ATD未記入・ETD窓内)')
        dep_filled = arr_filled = none = skipped = 0

        for s in rows:
            if maxc and self.credits >= maxc:
                self.stdout.write(self.style.WARNING(f'クレジット上限 {maxc} 到達。停止。'))
                break
            if not s.etd:
                skipped += 1
                continue
            uuid, st = self._resolve_uuid(s.vessel)
            if st != 'ok':
                skipped += 1
                self.stdout.write(f'  SKIP #{s.pk} {s.vessel}/{s.voyage} — 船名解決:{st}')
                continue
            d = self._history(uuid, s.etd, opt['before'], opt['after'])
            pos = (d.get('data') or {}).get('positions') or []
            arr_ut, dep_ut, last = self._visit(pos, radius, dep_speed)
            if not arr_ut:
                none += 1
                self.stdout.write(f'  NONE #{s.pk} {s.vessel}/{s.voyage} ETD{s.etd} — 上海寄港を検出できず')
                continue
            arr = (arr_ut + datetime.timedelta(hours=9)).date()   # JST日付(既存実績と統一)
            dep = (dep_ut + datetime.timedelta(hours=9)).date() if dep_ut else None
            # 別航海誤検出ガード: 着岸が予定ETDより大幅前(>4日)なら今回の積込ではない
            if s.etd and arr < s.etd - datetime.timedelta(days=4):
                none += 1
                self.stdout.write(f'  NONE #{s.pk} {s.vessel}/{s.voyage} ETD{s.etd} — 検出寄港({arr})が前航海とみなし除外')
                continue
            fields = []
            tags = []
            if s.shanghai_ata is None:
                tags.append(f'上海入港={arr}')
                if not opt['dry_run']:
                    s.shanghai_ata = arr
                    fields.append('shanghai_ata')
                arr_filled += 1
            if dep and s.atd is None:
                delay = (dep - s.etd).days if s.etd else None
                tags.append(f'ATD={dep}' + (f'(予定比{delay:+d}日)' if delay is not None else ''))
                if not opt['dry_run']:
                    s.atd = dep
                    fields.append('atd')
                dep_filled += 1
            state = '在港(荷役中)' if not dep else '出港済'
            mark = '' if opt['dry_run'] else '✓'
            msg = (f'  上海{mark} #{s.pk} {s.vessel}/{s.voyage} {s.dest} ETD{s.etd} '
                   f'-> [{state}] ' + ' '.join(tags) +
                   f' [最終測位 {(_utc(last)+datetime.timedelta(hours=9)):%m-%d %H:%M}JST {_dist_sh(last):.1f}km]')
            self.stdout.write(self.style.SUCCESS(msg) if tags else msg)
            if fields and not opt['dry_run']:
                fields.append('updated_at')
                s.save(update_fields=fields)

        self.stdout.write('')
        head = '【ドライラン】' if opt['dry_run'] else '【実行】'
        self.stdout.write(self.style.SUCCESS(
            f'{head} ATD記入 {dep_filled} / 上海入港記入 {arr_filled} / 寄港未検出 {none} '
            f'/ スキップ {skipped} / 消費クレジット約 {self.credits}'))
