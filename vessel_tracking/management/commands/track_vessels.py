"""就航中の本船を Datalastic(vessel_pro) で追跡し、ライブ動向を Shipment に保存する。

対象 = 取消でない・本船名あり・着地未確定(ata 未入力)の便。同一本船は1回だけ問い合わせ、
その本船の全対象便へスナップショット(現在地・速度・申告仕向・申告ETA)を反映する。

ライブETA・遅延予測の判定はモデル側(live_phase / live_alert)で行う。本コマンドは取得と保存のみ。

  python manage.py track_vessels
  python manage.py track_vessels --max-credits 50
  python manage.py track_vessels --pk 120

将来 scheduler から定期実行する想定(現状は手動 / 任意起動)。
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
from django.utils import timezone

from vessel_tracking.models import Shipment

BASE = 'https://api.datalastic.com/api/v0/'
# 着地港の中心座標(到着=ATA 自動記入の判定用)。
DEST_CENTER = {'JPTYO': (35.61168, 139.8268), 'JPOSA': (34.61406, 135.4404)}
# 申告仕向(AIS)が出荷の仕向地と「同一港湾圏」とみなせる UN/LOCODE 群。
# 東京湾(東京/横浜)・大阪湾(大阪/神戸)は実務上同一圏で、AIS は隣接港(例:東京便でも横浜)を
# 申告し得る。ATD(上海出港→日本行き)判定は厳密一致でなくこの圏内一致で行う。
DEST_GROUP = {'TOKYO': {'JPTYO', 'JPYOK'}, 'OSAKA': {'JPOSA', 'JPUKB'}}
# 別航海の誤記入ガード(本船は1〜2週で周回するため、同名異航海の「現在位置」で旧便を誤完了させない)。
MAX_TRANSIT_DAYS = 30    # 出港(ATD)→着地(ATA)の上限。これを超える現在停泊は別航海とみなしATA記入しない。
MAX_DEP_SLIP_DAYS = 30   # 予定ETDからの出港ずれ上限。これを超える出港は別航海とみなしATD記入しない。


def _haversine(la1, lo1, la2, lo2):
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _jst_date(dt):
    return dt.astimezone(datetime.timezone(datetime.timedelta(hours=9))).date() if dt else None


class Command(BaseCommand):
    help = '就航中の本船を vessel_pro で追跡し、ライブ動向を保存する'

    def add_arguments(self, parser):
        parser.add_argument('--api-key', default=None)
        parser.add_argument('--pk', type=int, default=None)
        parser.add_argument('--max-credits', type=int, default=0)
        parser.add_argument('--sleep', type=float, default=0.2)

    def _api(self, ep, **params):
        params['api-key'] = self.key
        url = BASE + ep + '?' + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                from vessel_tracking.datalastic_ssl import api_ssl_context
                with urllib.request.urlopen(url, timeout=60, context=api_ssl_context()) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(2 + attempt * 2)
                    continue
                raise CommandError(f'{ep} HTTP {e.code}: {e.read().decode()[:200]}')
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
            return None   # 一時失敗はキャッシュしない
        cand = [v for v in vessels
                if (v.get('type_specific') or '').lower().startswith('container')] or vessels
        uuid = cand[0]['uuid'] if len(cand) == 1 else None
        self._uuid_cache[name] = uuid
        return uuid

    @staticmethod
    def _parse_dt(s):
        if not s:
            return None
        try:
            return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
        except ValueError:
            return None

    def handle(self, *args, **opt):
        self.key = self._read_key(opt['api_key'])
        self._uuid_cache = {}
        maxc = opt['max_credits']

        qs = Shipment.objects.filter(is_cancelled=False, ata__isnull=True).exclude(vessel='')
        if opt['pk']:
            qs = qs.filter(pk=opt['pk'])
        rows = list(qs)

        # 接続・認証の事前チェック(失敗理由を明確化。握りつぶして「更新0」になるのを防ぐ)。
        if rows:
            try:
                self._api('vessel_find', name='CONSILIA')
            except CommandError as e:
                raise CommandError(
                    f'AIS接続/認証に失敗しました(APIキーまたはネットワークを確認): {e}')
        # 本船名でグルーピング(同一本船は1回の問い合わせで複数便に反映)
        by_vessel = {}
        for s in rows:
            by_vessel.setdefault(s.vessel, []).append(s)

        self.stdout.write(f'追跡対象: {len(rows)} 便 / {len(by_vessel)} 隻(本船名あり・着地未確定)')
        credits = updated = autorec = skipped = 0
        now = timezone.now()

        for vessel, ships in by_vessel.items():
            if maxc and credits >= maxc:
                self.stdout.write(self.style.WARNING(f'クレジット上限 {maxc} 到達。停止。'))
                break
            uuid = self._resolve_uuid(vessel)
            credits += 1   # vessel_find(初回のみ実課金。概算)
            if not uuid:
                skipped += 1
                self.stdout.write(f'  SKIP {vessel} — 船名解決不可')
                continue
            d = self._api('vessel_pro', uuid=uuid).get('data') or {}
            credits += 1
            time.sleep(opt['sleep'])
            eta = self._parse_dt(d.get('eta_UTC'))
            atd_dt = self._parse_dt(d.get('atd_UTC'))
            dep_u = (d.get('dep_port_unlocode') or '').upper()
            dest_u = (d.get('dest_port_unlocode') or '').upper()
            lat, lon, spd = d.get('lat'), d.get('lon'), d.get('speed')
            for s in ships:
                # --- ライブスナップショット保存 ---
                s.live_lat = lat
                s.live_lon = lon
                s.live_speed = spd
                s.live_dest_unlocode = (d.get('dest_port_unlocode') or '')
                s.live_dest_name = (d.get('dest_port') or '')
                s.live_eta = eta
                s.live_nav_status = (d.get('navigation_status') or '')
                s.live_updated_at = now
                fields = ['live_lat', 'live_lon', 'live_speed', 'live_dest_unlocode',
                          'live_dest_name', 'live_eta', 'live_nav_status',
                          'live_updated_at', 'updated_at']

                # --- AIS実績の自動記入 ---
                target = s.target_unlocode  # JPTYO / JPOSA
                auto = []
                # (1) 上海出港 → 着地 のレグなら ATD・上海入港 を自動記入(予定日に依存しない)
                #     ただし実出港が予定ETDより大幅に前(>3日)/大幅に後(>上限)なら別航海とみなし弾く
                #     (本船は1〜2週で周回するため、前/次航海の出港を誤記入しない)。
                #     申告仕向は同一港湾圏(東京湾=東京/横浜 等)を許容する。
                ad_cand = _jst_date(atd_dt) if atd_dt else None
                wrong_voyage = (ad_cand and s.etd and (
                    ad_cand < s.etd - datetime.timedelta(days=3)
                    or ad_cand > s.etd + datetime.timedelta(days=MAX_DEP_SLIP_DAYS)))
                dest_grp = DEST_GROUP.get(s.dest, {target} if target else set())
                if (s.atd is None and dep_u == 'CNSHG' and dest_grp and dest_u in dest_grp
                        and atd_dt and not wrong_voyage):
                    ad = ad_cand
                    s.atd = ad
                    fields.append('atd')
                    auto.append(f'ATD={ad}')
                    if s.shanghai_ata is None:   # 在港半日〜1日のため到着≒出発(同日近似)
                        s.shanghai_ata = ad
                        fields.append('shanghai_ata')
                        auto.append(f'上海入港={ad}')
                # (2) 着地港に着岸(停船)していれば ATA を自動記入。
                #     別航海ガード: 出港(ATD)から着地まで通常数日。何十日も後の「現在停泊」は
                #     同名異航海の寄港なので記入しない(旧便のATA未記入が現航海の着岸で誤充填されるのを防ぐ)。
                recent_arrival = s.atd is not None and (_jst_date(now) - s.atd).days <= MAX_TRANSIT_DAYS
                if (s.ata is None and recent_arrival and target in DEST_CENTER
                        and lat is not None and lon is not None and (spd or 0) < 1.5):
                    cy, cx = DEST_CENTER[target]
                    if _haversine(lat, lon, cy, cx) <= 25:
                        ad = _jst_date(now)
                        s.ata = ad
                        fields.append('ata')
                        auto.append(f'ATA={ad}')

                s.save(update_fields=fields)
                updated += 1
                if auto:
                    autorec += 1
                _lvl, label = s.live_departure_predict
                tag = ('  自動記入[' + ' '.join(auto) + ']') if auto else ''
                self.stdout.write(
                    f'  {vessel}/{s.voyage} {s.dest}: 仕向={d.get("dest_port")} '
                    f'ETA={d.get("eta_UTC")} 速度{spd} -> [{label}]{tag}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'更新 {updated} 便 / 自動記入 {autorec} 便 / 船名解決不可 {skipped} 隻 '
            f'/ {len(by_vessel)} 隻照会 / 消費クレジット約 {credits}'))
