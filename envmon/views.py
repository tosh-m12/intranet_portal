# envmon/views.py
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
import urllib.parse
import requests
from requests import Timeout, RequestException
import csv

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.encoding import smart_str, escape_uri_path
from django.utils.timezone import localtime
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.urls import reverse
import logging
from threading import Lock

from .models import (
    DeviceAssignment, Location, EnvSettings,
    AssignmentHistory, DeviceHistory,   # ★ 追加
)


API_TIMEOUT = 30

logger = logging.getLogger(__name__)


# ==== デバイスAPI用 トークンキャッシュ ====
_DEVICE_TOKEN_CACHE = {
    "token": None,
    "user_id": None,
    "fetched_at": None,
}
_DEVICE_TOKEN_LOCK = Lock()
DEVICE_TOKEN_TTL_SECONDS = 300  # 例：5分有効


class DeviceApiTokenInvalid(Exception):
    """デバイス一覧APIで code=104(token invalid) が返ってきた時用"""
    pass


def get_device_api_token(force_refresh: bool = False) -> tuple[str, int]:
    """
    デバイスAPI用のトークンをキャッシュ付きで取得する。
    - force_refresh=True のときは強制ログインし直し
    - それ以外は TTL 以内ならキャッシュを返す
    """
    from django.utils import timezone as dj_timezone  # 循環参照避け

    now = dj_timezone.now()

    with _DEVICE_TOKEN_LOCK:
        cached = _DEVICE_TOKEN_CACHE
        if (
            not force_refresh
            and cached["token"]
            and cached["fetched_at"]
            and (now - cached["fetched_at"]).total_seconds() < DEVICE_TOKEN_TTL_SECONDS
        ):
            return cached["token"], cached["user_id"]

        # 新しくログインしてキャッシュを更新
        token, user_id = login_and_get_token()
        cached["token"] = token
        cached["user_id"] = user_id
        cached["fetched_at"] = now
        return token, user_id


# ==== 外部 API 関連定数（Flask から移植） ====
LOGIN_URL = "https://1weilian.com/user/login"              # ★ http → https
DATA_URL  = "https://www.1weilian.com/public/realTimeData" # ★ host も統一
ACCOUNT = "nglswhs47"
PASSWORD = "ngls1234"

# ==== キャッシュファイルの場所 ====
# ひとまず「プロジェクトルート/cache/」を Flask 時代と同じ名前で利用する想定
BASE_DIR = Path(settings.BASE_DIR)
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)  # なければ作成




# ==== 共通ユーティリティ ====


def load_latest_cache() -> tuple[datetime | None, list[dict]]:
    """
    cache/device_cache_latest.json を読み込んで (更新日時, データ配列) を返す。
    なければ (None, [])。
    """
    latest_file = CACHE_DIR / "device_cache_latest.json"
    if not latest_file.exists():
        return None, []

    try:
        with latest_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        ts = datetime.fromtimestamp(latest_file.stat().st_mtime)
        return ts, data
    except Exception as e:
        print(f"[ERROR] Failed to load latest cache: {e}")
        return None, []


def login_and_get_token():
    """
    1weilian ログイン用。
    cache_worker_dj.py と同等: 最大3回まで POST のままリダイレクトを追う。
    """
    payload = {
        "account": ACCOUNT,
        "pwd": PASSWORD,
        "systemVersion": "PC",
        "loginType": 2,
    }
    headers = {"Content-Type": "application/json;charset=UTF-8"}

    url = LOGIN_URL
    resp = None

    for i in range(3):  # 最大3回まで自前でリダイレクト処理
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=API_TIMEOUT,
            allow_redirects=False,  # ★ 自動リダイレクト禁止（POST→GET化を防ぐ）
        )

        # 30x 系なら Location 見てもう一度 POST
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                break  # Location がない謎リダイレクト → ここで諦める

            new_url = urllib.parse.urljoin(url, loc)
            print(f"[LOGIN-DEBUG] redirect {resp.status_code} -> {new_url}")
            url = new_url
            continue  # ループ継続して再POST

        # それ以外（たぶん 200）ならループ終了
        break

    if resp is None:
        raise RuntimeError("Login request did not return any response")

    # 最終レスポンスを JSON として解釈
    try:
        result = resp.json()
    except ValueError:
        logger.debug("[LOGIN-DEBUG] 非JSONレスポンス: %s %s", resp.status_code, resp.text[:500])
        raise RuntimeError("Login API returned non-JSON response")

    logger.debug("[LOGIN-DEBUG] status: %s", resp.status_code)
    logger.debug("[LOGIN-DEBUG] body: %s", json.dumps(result, ensure_ascii=False)[:500])

    if "data" not in result:
        raise RuntimeError(f"Login API error. 'data' key not found. response={result}")

    return result["data"]["accessToken"], result["data"]["userId"]


def fetch_all_devices(token: str, user_id: int) -> list[dict]:
    """
    外部 API から全デバイス情報をページング取得する。
    途中のページでタイムアウトしても、それまでに取得できた分は返す。
    """
    all_devices: list[dict] = []
    page = 0

    while True:
        payload = {
            "userId": user_id,
            "loginType": 2,
            "accessToken": token,
            "permissions": 2,
            "language": 1,
            "page": page,
            "rows": 20,
            "sortingType": 0,
        }
        headers = {"Content-Type": "application/json;charset=UTF-8"}

        try:
            resp = requests.post(
                DATA_URL,
                json=payload,
                headers=headers,
                timeout=API_TIMEOUT,
                allow_redirects=False,
            )

            # データ側も 30x の可能性があるので一応対応
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                data_url2 = urllib.parse.urljoin(DATA_URL, loc)
                logger.debug("[FETCH-DEBUG] redirect %s -> %s", resp.status_code, data_url2)
                resp = requests.post(
                    data_url2,
                    json=payload,
                    headers=headers,
                    timeout=API_TIMEOUT,
                    allow_redirects=False,
                )

            try:
                result = resp.json()
            except ValueError:
                logger.debug(
                    "[FETCH-DEBUG] 非JSONレスポンス: %s %s",
                    resp.status_code,
                    resp.text[:500],
                )
                break

            if page == 0:
                logger.debug("[FETCH-DEBUG] status: %s", resp.status_code)
                logger.debug(
                    "[FETCH-DEBUG] body: %s",
                    json.dumps(result, ensure_ascii=False)[:500],
                )

            # ★ code チェックを追加
            code = result.get("code")
            if code == 104:
                # トークン無効 → 上位で再ログインさせる
                raise DeviceApiTokenInvalid("token invalid in fetch_all_devices")
            if code != 0:
                logger.error("[FETCH-ERROR] device API error: %s", result)
                break

            if "data" not in result or "dataList" not in result["data"]:
                logger.error(
                    "[ERROR] Unexpected device API response (no data.dataList): %s",
                    result,
                )
                break

            data_list = result["data"]["dataList"]
            if not data_list:
                break

            for dev in data_list:
                all_devices.append(
                    {
                        "id": dev["sn"],
                        "name": dev["deviceName"],
                        "temperature": dev["temperature"] if dev["status"] == 0 else "",
                        "humidity": dev["humidity"] if dev["status"] == 0 else "",
                        "last_seen": dev["date"],
                        "online": dev["status"] == 0,
                    }
                )

            page += 1

        except requests.Timeout as e:
            logger.warning(
                "[ERROR] Timeout when fetching page %s: %s", page, e
            )
            logger.info(
                "[INFO] Using partial device list so far. count = %s",
                len(all_devices),
            )
            break
        except DeviceApiTokenInvalid:
            # 上位にそのまま投げる（data_api で再ログイン＆リトライ）
            raise
        except Exception as e:
            logger.error(
                "[ERROR] Fetch devices page %s failed: %s", page, e
            )
            logger.info(
                "[INFO] Using partial device list so far. count = %s",
                len(all_devices),
            )
            break

    return all_devices


def attach_assignments(devices: list[dict]) -> None:
    """
    devices の各要素に DB 上の割当情報をくっつける。
    - location_id: Location.code
    - warehouse: Location.name または "未割当"
    """
    # device_id 一括取得のために辞書化
    id_to_device: dict[str, dict] = {}
    for d in devices:
        device_id = str(d.get("id"))
        id_to_device[device_id] = d

    if not id_to_device:
        return

    # まとめて取得
    assignments = (
        DeviceAssignment.objects.select_related("location")
        .filter(device_id__in=id_to_device.keys())
    )

    for assign in assignments:
        dev = id_to_device.get(assign.device_id)
        if not dev:
            continue
        loc = assign.location
        if loc:
            dev["location_id"] = loc.code
            dev["warehouse"] = loc.name
        else:
            dev["location_id"] = None
            dev["warehouse"] = "未割当"

    # 割当のないデバイスには明示的に "未割当" をセット
    for device_id, dev in id_to_device.items():
        if "warehouse" not in dev:
            dev["location_id"] = None
            dev["warehouse"] = "未割当"


# ==== View 関数 ====

@login_required
def index(request: HttpRequest) -> HttpResponse:
    """
    倉庫別 温湿度モニター（トップ画面）

    ※ 画面のデータ表示は JS から /envmon/data_api を叩いて
       リアルタイム取得する方式に変更。
       ここでは表示更新間隔 interval だけテンプレートに渡す。
    """
    env = EnvSettings.get_solo()
    interval = env.interval

    context = {
        "interval": interval,
    }
    return render(request, "envmon/index.html", context)



@login_required
def all_devices(request: HttpRequest) -> HttpResponse:
    """
    全デバイス稼働状況一覧
    - 最新キャッシュから全デバイスを取得
    - DB 割当を付加して一覧表示
    """
    dt, devices = load_latest_cache()
    attach_assignments(devices)

    context = {
        "devices": devices,
        "last_updated": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "N/A",
    }
    return render(request, "envmon/all_devices.html", context)


@login_required
def data_api(request: HttpRequest) -> JsonResponse:
    """
    フロント用 JSON API
    - 外部 API に直接アクセスして全デバイス取得
    - DB 割当情報を付加して返却
    ※ ここでは EnvLog や DeviceHistory は使わない（リアルタイム専用）
    """
    try:
        # ★ トークンをキャッシュ付きで取得
        token, user_id = get_device_api_token()

        try:
            devices = fetch_all_devices(token, user_id)
        except DeviceApiTokenInvalid:
            # ★ token invalid のときだけ 1回だけ再ログインしてリトライ
            logger.warning("[envmon] token invalid -> relogin and retry once")
            token, user_id = get_device_api_token(force_refresh=True)
            devices = fetch_all_devices(token, user_id)

        # 倉庫割当情報を付加（warehouse, location_id）
        attach_assignments(devices)

        return JsonResponse(devices, safe=False)

    except Exception as e:
        # ここにくるのは「ネットワーク完全ダウン」など本当に致命的な場合
        logger.error("[envmon] data_api error: %s", e)
        return JsonResponse({"error": str(e)}, status=500)
    

# ========================
# 設定画面
# ========================
@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    env = EnvSettings.get_solo()

    if request.method == "POST":
        ...
        env.save()
        return redirect("envmon:index")

    # ==== ここから履歴用 SN リスト作成 ====
    history_sns = list(
        DeviceHistory.objects.order_by("sn")
        .values_list("sn", flat=True)
        .distinct()
    )

    _dt, devices = load_latest_cache()
    attach_assignments(devices)

    id_to_warehouse = {}
    for d in devices:
        sn = d.get("id")
        wh = d.get("warehouse") or "未割当"
        if sn:
            id_to_warehouse[sn] = wh

    device_choices = []
    for sn in history_sns:
        wh = id_to_warehouse.get(sn, "不明")
        label = f"{sn}（{wh}）"
        device_choices.append({"sn": sn, "label": label})

    # ★ 倉庫ロケーション一覧を追加
    locations = Location.objects.order_by("code")

    context = {
        "interval": env.interval,
        "cache_interval": env.cache_interval,
        "cache_expire_hours": env.cache_expire_hours,
        "log_times": env.log_times,
        "log_directory": env.log_directory,
        "device_choices": device_choices,
        "history_fetch_time": env.history_fetch_time,
        "is_fetching_history": env.is_fetching_history,
        "locations": locations,   # ★ 追加
    }
    return render(request, "envmon/settings.html", context)


# ★ トークン無効時に使う専用例外
class TokenInvalidError(Exception):
    pass


# 履歴用 API の URL（1weilian 履歴API）
HISTORY_URL = "https://www.1weilian.com/historical/selectHistoryData"


def fetch_history_for_sn(
    token: str,
    user_id: int,
    sn: str,
    start_time_str: str | None = None,
    full_mode: bool = False,
) -> list[dict]:
    """
    1weilian の「履歴データ」APIから、1つの SN の履歴をページング取得するヘルパー。

    - start_time_str: "YYYY-MM-DD HH:MM:SS" 形式の文字列（None/空なら全期間）
    - full_mode = False:
        -> page=0 の 1ページだけ取得（通常の日次運転用）
    - full_mode = True:
        -> データが続く限り page を最後までめくる（フルキャッチアップ用）

    ※ code=104(token invalid) の場合は TokenInvalidError を投げる。
    """
    all_rows: list[dict] = []
    page = 0

    # ★ Django のデフォルトタイムゾーン（settings.TIME_ZONE）を使用
    default_tz = timezone.get_default_timezone()

    while True:
        payload = {
            "userId": user_id,
            "loginType": 2,
            "accessToken": token,
            "permissions": 2,
            "subUserId": None,
            "sn": sn,
            "deviceName": None,
            "sharedUserId": None,
            "regionalId": None,
            "language": 1,
            "isShare": None,
            "rows": 200,      # 1ページあたりの件数
            "page": page,     # 0 ベース
            "startTime": "",
            "endTime": "",
        }

        # page=0 かつ start_time_str があれば startTime にセット
        if page == 0 and start_time_str:
            payload["startTime"] = start_time_str
            print(f"[HISTORY-DEBUG] sn={sn} page={page} をリクエスト (startTime={start_time_str})")
        else:
            print(f"[HISTORY-DEBUG] sn={sn} page={page} をリクエスト")

        headers = {"Content-Type": "application/json;charset=UTF-8"}

        try:
            resp = requests.post(
                HISTORY_URL,
                json=payload,
                headers=headers,
                timeout=API_TIMEOUT,
            )
        except Timeout as e:
            print(f"[HISTORY-DEBUG] TIMEOUT sn={sn} page={page}: {e}")
            break
        except RequestException as e:
            print(f"[HISTORY-DEBUG] REQUEST ERROR sn={sn} page={page}: {e}")
            break

        if resp.status_code != 200:
            print(
                "[HISTORY-DEBUG] HTTP error sn=%s page=%s status=%s body=%s"
                % (sn, page, resp.status_code, resp.text[:200])
            )
            break

        try:
            result = resp.json()
        except ValueError:
            print("[HISTORY-DEBUG] 非JSONレスポンス:", resp.status_code, resp.text[:500])
            break

        code = result.get("code")

        # ★ token invalid (104) の扱いを page で分ける
        if code == 104:
            if page == 0:
                # 本当にトークンが死んでいる可能性が高いので、上位で再ログイン
                print(f"[HISTORY-DEBUG] token invalid sn={sn} page={page}: {result}")
                raise TokenInvalidError("token invalid")
            else:
                # page>0 で 104 が出るのは「これ以上ページングできない/履歴上限」の可能性が高い
                print(f"[HISTORY-DEBUG] token invalid (page>0) sn={sn} page={page}: {result} → ここで打ち切り")
                break

        if code != 0:
            print("[HISTORY-DEBUG] API error sn=%s page=%s: %s" % (sn, page, result))
            break

        try:
            data = result["data"]
            data_list = data["dataList"]
        except Exception as e:
            print("[HISTORY-DEBUG] 想定外レスポンス sn=%s page=%s: %s %s"
                  % (sn, page, e, result))
            break

        if not data_list:
            # データが空なら最終ページ
            break

        for row in data_list:
            ts_str = row.get("date")  # 例: "2025-12-04 21:33:00"
            try:
                naive_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                # ★ naive -> aware (settings.TIME_ZONE ベース)
                if timezone.is_naive(naive_dt):
                    recorded_at = timezone.make_aware(naive_dt, default_tz)
                else:
                    recorded_at = naive_dt
            except Exception:
                print("[HISTORY-DEBUG] 日付パース失敗:", ts_str)
                continue

            temperature = None
            humidity = None
            try:
                if row.get("temperature") is not None:
                    temperature = float(row["temperature"])
            except Exception:
                pass

            try:
                if row.get("humidity") is not None:
                    humidity = float(row["humidity"])
            except Exception:
                pass

            all_rows.append(
                {
                    "recorded_at": recorded_at,
                    "temperature": temperature,
                    "humidity": humidity,
                    "raw": row,
                }
            )

        # ★ full_mode=False の場合は 1ページだけ取って終了
        if not full_mode:
            print(f"[HISTORY-DEBUG] sn={sn} 1ページ取得で打ち切り rows={len(data_list)}")
            break

        page += 1

    return all_rows


def fetch_history_all_core(from_scheduler: bool = False) -> int:
    """
    全デバイスについて履歴を取得して DeviceHistory に追加する共通ロジック。
    - 通常は「前回記録時刻からの増分」を 1ページだけ取得
    - 前回記録時刻から現在までのギャップが大きい場合は full_mode で全ページ取得
    - EnvSettings.is_fetching_history で二重起動を防止する
    """

    # ★★★ 二重起動防止用フラグ制御（トランザクション＆行ロック） ★★★
    with transaction.atomic():
        # EnvSettings.get_solo() は id=1 で get_or_create している想定
        env = EnvSettings.objects.select_for_update().get(pk=EnvSettings.get_solo().pk)

        if env.is_fetching_history:
            print(f"[envmon] fetch_env_history already running (from_scheduler={from_scheduler}), skip.")
            return 0

        env.is_fetching_history = True
        env.save(update_fields=["is_fetching_history"])

    try:
        # ===== ここからは従来のロジック（ほぼそのまま） =====
        try:
            token, user_id = login_and_get_token()
        except Exception as e:
            print(f"[envmon] fetch_env_history login failed: {e}")
            raise

        # 現在のキャッシュから SN 一覧を取得
        _dt, devices = load_latest_cache()
        sns: list[str] = []
        for d in devices:
            sn = d.get("id")
            if sn and sn not in sns:
                sns.append(sn)

        total_new = 0
        now_local = timezone.localtime(timezone.now())
        GAP_THRESHOLD = timedelta(hours=24)  # ★ 24時間を閾値にする

        for sn in sns:
            # その SN の最後の recorded_at を取得
            last_row = (
                DeviceHistory.objects.filter(sn=sn)
                .order_by("-recorded_at")
                .first()
            )

            start_time_str: str | None = None
            full_mode: bool = False

            if last_row:
                # tz-aware → ローカルタイムに変換
                last_local = timezone.localtime(last_row.recorded_at)
                gap = now_local - last_local

                # ★ 常に「最後に取った時刻」から先を取りに行く
                start_time_str = last_local.strftime("%Y-%m-%d %H:%M:%S")

                if gap > GAP_THRESHOLD:
                    # ★ ギャップが大きいときは FULL_MODE（= 複数ページ）で追いかける
                    full_mode = True
                    print(
                        f"[HISTORY-DEBUG] sn={sn} startTime={start_time_str} "
                        f"(gap={gap}) → FULL_MODE で複数ページ取得"
                    )
                else:
                    # ★ 通常運転：1ページだけ
                    print(
                        f"[HISTORY-DEBUG] sn={sn} startTime={start_time_str} (gap={gap})"
                    )
            else:
                # DB にまだ一件もない → 初回フル取得（全期間）
                full_mode = True
                start_time_str = None  # 全期間
                print(f"[HISTORY-DEBUG] sn={sn} は初回取得 → FULL_MODE（全期間）")

            # ★ token invalid 対応：SNごとに 1 回だけ再ログインしてリトライ
            rows: list[dict] = []
            retry_login = False

            while True:
                try:
                    rows = fetch_history_for_sn(
                        token,
                        user_id,
                        sn,
                        start_time_str=start_time_str,
                        full_mode=full_mode,
                    )
                    break  # 正常に取得できたらループ脱出

                except TokenInvalidError as e:
                    if retry_login:
                        print(f"[HISTORY-DEBUG] token invalid 再ログイン後も失敗 sn={sn}: {e}")
                        rows = []
                        break

                    print(f"[HISTORY-DEBUG] token invalid → 再ログインしてリトライ sn={sn}")
                    try:
                        token, user_id = login_and_get_token()
                        retry_login = True
                        continue  # もう一度 fetch_history_for_sn を呼ぶ
                    except Exception as e2:
                        print(f"[HISTORY-DEBUG] 再ログイン失敗 sn={sn}: {e2}")
                        rows = []
                        break

                except Exception as e:
                    print(f"[HISTORY-DEBUG] 想定外エラー sn={sn}: {e}")
                    rows = []
                    break

            if not rows:
                # この SN では何も取得できなかった
                continue

            # DeviceHistory に保存
            objs = [
                DeviceHistory(
                    sn=sn,
                    recorded_at=row["recorded_at"],
                    temperature=row.get("temperature"),
                    humidity=row.get("humidity"),
                    raw=row.get("raw"),
                )
                for row in rows
            ]

            with transaction.atomic():
                DeviceHistory.objects.bulk_create(
                    objs,
                    ignore_conflicts=True,  # UniqueConstraint で重複はスキップ
                )

            total_new += len(objs)

        print(f"[envmon] fetch_env_history done (attempted inserts: {total_new})")
        return total_new

    finally:
        # ★★★ どんな結果でも必ずフラグを解除する ★★★
        env = EnvSettings.get_solo()
        env.is_fetching_history = False
        env.save(update_fields=["is_fetching_history"])


@login_required
@require_POST
def fetch_history_all(request: HttpRequest) -> HttpResponse:
    """
    （手動ボタン用）
    """
    try:
        total_new = fetch_history_all_core()

        if total_new == 0:
            # 追加データなし or すでに別の処理が動いていてスキップ
            messages.info(
                request,
                "履歴データの追加はありませんでした（既に処理済み、または現在別の取得処理が実行中の可能性があります）。"
            )
        else:
            messages.success(
                request,
                f"履歴データの取得処理を実行しました。（投入試行件数: {total_new}）"
            )
    except Exception as e:
        messages.error(request, f"履歴データの取得に失敗しました: {e}")

    return redirect("envmon:settings")


# ========================
# 倉庫名編集
# ========================
@login_required
def edit_locations(request: HttpRequest) -> HttpResponse:
    locations = Location.objects.order_by("code")

    if request.method == "POST":
        # フォーム名: loc_<code> に合わせる
        for loc in locations:
            field_name = f"loc_{loc.code}"
            new_name = request.POST.get(field_name)
            if new_name is not None:
                new_name = new_name.strip()
                if new_name:
                    loc.name = new_name
                    loc.save()
        return redirect("envmon:locations")

    context = {
        "locations": locations,
    }
    return render(request, "envmon/locations.html", context)


# ========================
# 倉庫ごとのデバイス割当
# ========================
@login_required
def warehouse_assign(request: HttpRequest) -> HttpResponse:
    """
    倉庫ごとのデバイス割当画面

    ※ ここでは外部APIにはアクセスせず、
       cache/device_cache_latest.json の内容だけを使う。
       （シリアル＋デバイス名が分かれば十分）
    """

    # 最新キャッシュを読み込み
    _dt, devices = load_latest_cache()

    # もしキャッシュが空なら、その旨を表示して返す
    if not devices:
        context = {
            "locations": Location.objects.order_by("code"),
            "assignments_json": "{}",
            "unassigned_devices": [],
            "locations_json": "{}",
            "location_codes_json": "[]",
            "cache_empty": True,   # テンプレ側でメッセージ表示に使ってもよい
        }
        return render(request, "envmon/warehouse_assign.html", context)

    # キャッシュから ID → デバイス名 を作成
    device_names = {str(d.get("id")): d.get("name", "") for d in devices}

    # 現在の割当を取得
    assignments_qs = DeviceAssignment.objects.select_related("location").all()
    assignments_dict: dict[str, str] = {}  # device_id -> location_code
    for a in assignments_qs:
        if a.location:
            assignments_dict[a.device_id] = a.location.code

    # ロケーション一覧
    locations = Location.objects.order_by("code")

    # JS 用の補助データ
    locations_dict = {loc.code: loc.name for loc in locations}
    location_codes = [loc.code for loc in locations]

    # 未割当のデバイス一覧（キャッシュ側にあるが assignments_dict にないもの）
    assigned_ids = set(assignments_dict.keys())
    unassigned_devices: list[dict] = []
    for d in devices:
        dev_id = str(d.get("id") or "")
        if not dev_id:
            continue
        if dev_id not in assigned_ids:
            unassigned_devices.append(
                {
                    "id": dev_id,
                    "name": device_names.get(dev_id, ""),
                }
            )

    context = {
        "locations": locations,  # queryset
        "assignments_json": json.dumps(assignments_dict, ensure_ascii=False),
        "unassigned_devices": unassigned_devices,
        "locations_json": json.dumps(locations_dict, ensure_ascii=False),
        "location_codes_json": json.dumps(location_codes, ensure_ascii=False),
        "cache_empty": False,
    }
    return render(request, "envmon/warehouse_assign.html", context)


# ========================
# 割当保存 API（ドラッグ＆ドロップから呼ばれる）
# ========================
@login_required
@require_POST
def save_assignment(request: HttpRequest) -> JsonResponse:
    """
    JS から JSON を受けて device_id -> location_code のマッピングで保存する。
    受信したマッピングは「全体状態」なので、毎回まとめて反映する方式。
    """
    try:
        body = request.body.decode("utf-8")
        data = json.loads(body)
        # data: {device_id: "loc06" or "external_loc02" or ""} の想定
    except Exception:
        return JsonResponse({"error": "invalid JSON"}, status=400)

    # 既存の割当を全て取得しておき、差分更新してもよいが、
    # ここでは簡単に「一度全削除して入れ直し」でもOK。
    # ただし履歴を残したいので、1件ずつ処理する。
    # （Flask 版も毎回全件 record していた）

    # ここでは「渡された device_id だけ」を対象とする。
    now = timezone.now()

    for device_id, loc_code in data.items():
        loc = None
        if loc_code:
            loc = Location.objects.filter(code=loc_code).first()

        if loc is None and not loc_code:
            # 完全に未割当。既存レコードがあれば削除。
            DeviceAssignment.objects.filter(device_id=device_id).delete()
            # 未割当状態の履歴を残したい場合はここで location=None で作成してもよいが、
            # Flask 時代は JSON に存在しないので、ここでは履歴を残さないことにする。
            continue

        # loc がある場合は割当 or 更新
        DeviceAssignment.objects.update_or_create(
            device_id=device_id,
            defaults={"location": loc},
        )

        # 履歴を残す
        AssignmentHistory.objects.create(
            device_id=device_id,
            location=loc,
            changed_at=now,
        )

    return JsonResponse({"status": "ok"})

@login_required
@require_POST
def download_history_csv(request: HttpRequest) -> HttpResponse:
    """
    DeviceHistory から、指定SNの全期間履歴を CSV ダウンロードする。
    """
    sn = request.POST.get("sn")
    if not sn:
        messages.error(request, "シリアルナンバーが指定されていません。")
        return redirect("envmon:settings")

    qs = DeviceHistory.objects.filter(sn=sn).order_by("recorded_at")

    # CSV レスポンス
    filename = f"history_{sn}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    # 日本語ファイル名対応
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{escape_uri_path(filename)}"

    writer = csv.writer(response)
    writer.writerow(["SN", "記録日時", "温度", "湿度"])
    for row in qs:
        writer.writerow([
            row.sn,
            row.recorded_at.strftime("%Y-%m-%d %H:%M:%S"),
            row.temperature if row.temperature is not None else "",
            row.humidity if row.humidity is not None else "",
        ])

    return response


@login_required
@require_POST
def download_history_by_warehouse(request: HttpRequest) -> HttpResponse:
    """
    ② 倉庫単位：指定ロケーションに現在割り当てられているデバイスの
    履歴をまとめて CSV 出力。
    期間（date_from, date_to）は任意。両方空なら全期間。
    """
    location_code = request.POST.get("location_code")
    if not location_code:
        messages.error(request, "倉庫ロケーションが指定されていません。")
        return redirect("envmon:settings")

    location = Location.objects.filter(code=location_code).first()
    if not location:
        messages.error(request, f"指定されたロケーションが見つかりません: {location_code}")
        return redirect("envmon:settings")

    # 現在このロケーションに割り当てられているデバイスID一覧
    sns = list(
        DeviceAssignment.objects.filter(location=location)
        .values_list("device_id", flat=True)
    )
    if not sns:
        messages.info(request, f"ロケーション {location.code} / {location.name} に割当されたデバイスがありません。")
        return redirect("envmon:settings")

    # 期間指定のパース
    date_from_str = request.POST.get("date_from") or ""
    date_to_str = request.POST.get("date_to") or ""

    date_from = parse_date(date_from_str) if date_from_str else None
    date_to = parse_date(date_to_str) if date_to_str else None

    qs = DeviceHistory.objects.filter(sn__in=sns)

    default_tz = timezone.get_default_timezone()

    if date_from:
        start_dt = timezone.make_aware(
            datetime.combine(date_from, datetime.min.time()),
            default_tz,
        )
        qs = qs.filter(recorded_at__gte=start_dt)

    if date_to:
        # date_to の翌日の 00:00 までを含める
        end_dt = timezone.make_aware(
            datetime.combine(date_to + timedelta(days=1), datetime.min.time()),
            default_tz,
        )
        qs = qs.filter(recorded_at__lt=end_dt)

    qs = qs.order_by("sn", "recorded_at")

    if not qs.exists():
        messages.info(request, "指定条件に該当する履歴データがありません。")
        return redirect("envmon:settings")

    # CSV レスポンス
    df_label = date_from_str or "ALL"
    dt_label = date_to_str or "ALL"
    filename = f"history_warehouse_{location.code}_{df_label}_{dt_label}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{escape_uri_path(filename)}"

    writer = csv.writer(response)
    writer.writerow(["倉庫コード", "倉庫名", "SN", "記録日時", "温度", "湿度"])

    for row in qs:
        writer.writerow([
            location.code,
            location.name,
            row.sn,
            row.recorded_at.strftime("%Y-%m-%d %H:%M:%S"),
            row.temperature if row.temperature is not None else "",
            row.humidity if row.humidity is not None else "",
        ])

    return response


@login_required
@require_POST
def download_history_all_range(request: HttpRequest) -> HttpResponse:
    """
    ④ 全デバイス＋期間指定：DeviceHistory 全体から
    指定期間のデータをまとめて CSV 出力。
    """
    date_from_str = request.POST.get("date_from") or ""
    date_to_str = request.POST.get("date_to") or ""

    if not date_from_str or not date_to_str:
        messages.error(request, "開始日と終了日を指定してください。")
        return redirect("envmon:settings")

    date_from = parse_date(date_from_str)
    date_to = parse_date(date_to_str)

    if not date_from or not date_to:
        messages.error(request, "日付の形式が不正です。")
        return redirect("envmon:settings")

    if date_from > date_to:
        messages.error(request, "開始日は終了日以前の日付を指定してください。")
        return redirect("envmon:settings")

    default_tz = timezone.get_default_timezone()
    start_dt = timezone.make_aware(
        datetime.combine(date_from, datetime.min.time()),
        default_tz,
    )
    end_dt = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), datetime.min.time()),
        default_tz,
    )

    qs = (
        DeviceHistory.objects
        .filter(recorded_at__gte=start_dt, recorded_at__lt=end_dt)
        .order_by("sn", "recorded_at")
    )

    if not qs.exists():
        messages.info(request, "指定期間の履歴データが存在しません。")
        return redirect("envmon:settings")

    filename = f"history_all_{date_from_str}_{date_to_str}.csv"

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{escape_uri_path(filename)}"

    writer = csv.writer(response)
    writer.writerow(["SN", "記録日時", "温度", "湿度"])

    for row in qs:
        writer.writerow([
            row.sn,
            row.recorded_at.strftime("%Y-%m-%d %H:%M:%S"),
            row.temperature if row.temperature is not None else "",
            row.humidity if row.humidity is not None else "",
        ])

    return response
