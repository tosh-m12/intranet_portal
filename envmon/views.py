# envmon/views.py
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
import urllib.parse
import requests
from requests import Timeout, RequestException
import csv
from collections import defaultdict
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.encoding import smart_str, escape_uri_path
from django.utils.timezone import localtime
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
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


# ==============================
#  例外クラス
# ==============================
class DeviceApiTokenInvalid(Exception):
    """デバイス一覧APIで code=104(token invalid) が返ってきた時用"""
    pass


# ★ 履歴用 API で使うトークン無効例外
class TokenInvalidError(Exception):
    """履歴 API 用のトークン無効例外"""
    pass


# ==============================
#  デバイスAPI用 トークンキャッシュ
# ==============================
_DEVICE_TOKEN_CACHE = {
    "token": None,
    "user_id": None,
    "fetched_at": None,
}
_DEVICE_TOKEN_LOCK = Lock()
DEVICE_TOKEN_TTL_SECONDS = 300  # 例：5分有効


def get_device_api_token():
    """
    1weilian へログインし、accessToken と userId を返す。
    cache_worker_dj.py と同じリダイレクト対応ロジック。
    """
    payload = {
        "account": ACCOUNT,
        "pwd": PASSWORD,
        "systemVersion": "PC",
        "loginType": 2,
    }
    headers = {"Content-Type": "application/json;charset=UTF-8"}

    # まずはリダイレクトを追わずに投げる
    resp = requests.post(
        LOGIN_URL,
        json=payload,
        headers=headers,
        timeout=30,
        allow_redirects=False,
    )

    # 30x（リダイレクト）の場合は、自分で再度 POST する
    if resp.status_code in (301, 302, 303, 307, 308):
        loc = resp.headers.get("Location")
        if not loc:
            raise RuntimeError(f"Login redirect without Location header: {resp.status_code}")

        login_url2 = urllib.parse.urljoin(LOGIN_URL, loc)
        print(f"[LOGIN-DEBUG] redirect {resp.status_code} -> {login_url2}")

        resp = requests.post(
            login_url2,
            json=payload,
            headers=headers,
            timeout=30,
            allow_redirects=False,
        )

    # 最終レスポンスを JSON に
    try:
        result = resp.json()
    except ValueError:
        print("[LOGIN-DEBUG] 非JSONレスポンス:", resp.status_code, resp.text[:500])
        raise RuntimeError("Login API returned non-JSON response")

    print("[LOGIN-DEBUG] status:", resp.status_code)
    print("[LOGIN-DEBUG] body:", json.dumps(result, ensure_ascii=False)[:500])

    if "data" not in result:
        raise RuntimeError(f"Login API error. 'data' key not found. response={result}")

    data = result["data"]
    return data["accessToken"], data["userId"]



# ==============================
#  外部 API 関連定数（Flask から移植）
# ==============================
LOGIN_URL = "https://1weilian.com/user/login"              # ★ http → https
DATA_URL  = "https://www.1weilian.com/public/realTimeData" # ★ host も統一
HISTORY_URL = "https://www.1weilian.com/historical/selectHistoryData"

ACCOUNT = "nglswhs47"
PASSWORD = "ngls1234"


# ==============================
#  キャッシュファイルの場所
# ==============================
# ひとまず「プロジェクトルート/cache/」を Flask 時代と同じ名前で利用する想定
BASE_DIR = Path(settings.BASE_DIR)
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)  # なければ作成


# ==============================
#  共通ユーティリティ
# ==============================
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


def fetch_all_devices(token, user_id):
    """
    外部 API から全デバイスを取得。
    cache_worker_dj.py と同様、
    - ページング対応
    - タイムアウト時はそこで打ち切り、取れた分だけ返す
    - token エラー時は例外を投げる
    """
    all_devices = []
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
                timeout=30,
                allow_redirects=False,
            )

            # 30x の場合は自前で POST し直す
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                data_url2 = urllib.parse.urljoin(DATA_URL, loc)
                print(f"[FETCH-DEBUG] redirect {resp.status_code} -> {data_url2}")
                resp = requests.post(
                    data_url2,
                    json=payload,
                    headers=headers,
                    timeout=30,
                    allow_redirects=False,
                )

            try:
                result = resp.json()
            except ValueError:
                print("[FETCH-DEBUG] 非JSONレスポンス:", resp.status_code, resp.text[:500])
                break

            if page == 0:
                print("[FETCH-DEBUG] status:", resp.status_code)
                print("[FETCH-DEBUG] body:", json.dumps(result, ensure_ascii=False)[:500])

            # ここで code をチェックして token エラーを検知
            if result.get("code") != 0:
                # ここから「token invalid in fetch_all_devices」が出ていたので、
                # ログを出しつつ例外にする
                raise RuntimeError(f"device API error: {result}")

            if "data" not in result or "dataList" not in result["data"]:
                print("[ERROR] Unexpected device API response (no data.dataList):", result)
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
            print(f"[ERROR] Timeout when fetching page {page}: {e}")
            print("[INFO] Using partial device list so far. count =", len(all_devices))
            break
        except Exception as e:
            print(f"[ERROR] Fetch devices page {page} failed: {e}")
            print("[INFO] Using partial device list so far. count =", len(all_devices))
            break

    return all_devices


def attach_assignments(devices):
    """
    device dict に DB の倉庫割当情報を合成する。
    （キャッシュに既に warehouse がついていても上書きOK）
    """
    id_to_device = {}
    for d in devices:
        dev_id = str(d.get("id"))
        if not dev_id:
            continue
        id_to_device[dev_id] = d

    if not id_to_device:
        return

    qs = (
        DeviceAssignment.objects
        .select_related("location")
        .filter(device_id__in=id_to_device.keys())
    )

    for a in qs:
        dev = id_to_device.get(a.device_id)
        if not dev:
            continue
        loc = a.location
        if loc:
            dev["location_id"] = loc.code
            dev["warehouse"] = loc.name
        else:
            dev["location_id"] = None
            dev["warehouse"] = "未割当"

    # 未割当（DBに存在しないデバイス）にデフォルト値を入れる
    for dev_id, dev in id_to_device.items():
        if "warehouse" not in dev:
            dev["location_id"] = None
            dev["warehouse"] = "未割当"


def create_csv_response(filename: str) -> HttpResponse:
    """
    CSV ダウンロード用共通レスポンス作成ヘルパー。
    """
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    # 日本語ファイル名対応
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{escape_uri_path(filename)}"
    return response


# ==============================
#  View 関数
# ==============================

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
def data_api(request):
    """
    最新のリアルタイムデータを返す API。
    キャッシュ（device_cache_latest.json）専用。
    外部APIには一切アクセスしない。
    """

    cache_file = CACHE_DIR / "device_cache_latest.json"

    if not cache_file.exists():
        print("[data_api] cache file not found")
        return JsonResponse([], safe=False)

    # キャッシュ読み込み
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            devices = json.load(f)
        print(f"[data_api] loaded cache: {cache_file}, count={len(devices)}")
    except Exception as e:
        print(f"[data_api] failed to read cache: {e}")
        return JsonResponse([], safe=False)

    # DB割当付加（キャッシュには warehouse がない可能性があるため）
    attach_assignments(devices)

    # 未割当／未登録デバイスは除外
    filtered = []
    for d in devices:
        warehouse = (d.get("warehouse") or "").strip()
        name = (d.get("name") or "").strip()

        if warehouse == "未割当":
            continue
        if name.startswith("未登録:"):
            continue

        filtered.append(d)

    return JsonResponse(filtered, safe=False)


# ========================
# 設定画面
# ========================
@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    env = EnvSettings.get_solo()

    # ★ 管理者専用ガード
    if not request.user.is_staff:
        messages.error(
            request,
            "環境モニタの設定ページは管理者権限ユーザーのみアクセスできます。"
        )
        return redirect("envmon:index")

    if request.method == "POST":
        ...
        env.save()
        return redirect("envmon:settings")

    context = {
        "interval": env.interval,
        "cache_interval": env.cache_interval,
        "cache_expire_hours": env.cache_expire_hours,
        "log_times": env.log_times,
        "log_directory": env.log_directory,
        "history_fetch_time": env.history_fetch_time,
        "is_fetching_history": env.is_fetching_history,
    }
    return render(request, "envmon/settings.html", context)


@login_required
def history_csv_menu(request: HttpRequest) -> HttpResponse:
    """
    履歴 CSV ダウンロード専用ページ。
    一般ユーザーもアクセス可能（ログイン必須）。
    """

    # ==== 履歴用 SN リスト作成 ====
    history_sns = list(
        DeviceHistory.objects.order_by("sn")
        .values_list("sn", flat=True)
        .distinct()
    )

    # 最新キャッシュから倉庫名をひっぱる
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

    # 倉庫ロケーション一覧
    locations = Location.objects.order_by("code")

    context = {
        "device_choices": device_choices,
        "locations": locations,
    }
    return render(request, "envmon/history_download.html", context)


# ==============================
# 履歴取得ロジック
# ==============================
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
            "rows": 500,      # 1ページあたりの件数
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

    ※ 実行中フラグ (EnvSettings.is_fetching_history) の ON/OFF は
       management command 'fetch_env_history' 側で行う。
    """

    # ★★★ ここから下は、今まで try ブロックの中にあった本体ロジックだけ残す ★★★

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


@login_required
@require_POST
def fetch_history_all(request: HttpRequest) -> HttpResponse:
    """
    （旧）手動ボタン用。
    現在は management command 'fetch_env_history' を呼び出すだけにする。
    """
    try:
        call_command("fetch_env_history")
        messages.success(
            request,
            "履歴データの取得処理を実行しました。（旧 fetch_history_all ルート）"
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


# ========================
# CSV ダウンロード系
# ========================
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

    filename = f"history_{sn}.csv"
    response = create_csv_response(filename)

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

    response = create_csv_response(filename)

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

    response = create_csv_response(filename)

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


def load_cache_history_for_sns(sns_set, start_dt, end_dt):
    """
    キャッシュ(JSON)から指定SN群の履歴を読み込む。
    - sns_set: SNの集合（文字列）
    - start_dt, end_dt: 取り込み対象の期間（tz-aware）
    戻り値: { sn: [ {recorded_at, temperature, humidity}, ... ], ... }
    """
    sns_set = {str(sn) for sn in sns_set}
    if not sns_set:
        return {}

    default_tz = timezone.get_default_timezone()
    rows_by_sn: dict[str, list[dict]] = {sn: [] for sn in sns_set}

    for fname in os.listdir(CACHE_DIR):
        if not (fname.startswith("device_cache_") and fname.endswith(".json")):
            continue
        if fname == "device_cache_latest.json":
            # latest は timestamp 付きのファイルと中身が同じなのでスキップでもOK
            continue

        ts_str = fname.replace("device_cache_", "").replace(".json", "")
        try:
            ts_naive = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
        except Exception:
            continue

        # tz-aware に変換
        if timezone.is_naive(ts_naive):
            ts = timezone.make_aware(ts_naive, default_tz)
        else:
            ts = ts_naive

        # 7日分の対象期間外はスキップ
        if ts < start_dt or ts > end_dt:
            continue

        path = CACHE_DIR / fname
        try:
            with path.open("r", encoding="utf-8") as f:
                devices = json.load(f)
        except Exception as e:
            print(f"[CACHE-DEBUG] failed to load {path}: {e}")
            continue

        if not isinstance(devices, list):
            continue

        for d in devices:
            sn = str(d.get("id") or "")
            if sn not in sns_set:
                continue

            # 温湿度を float or None に正規化
            t_raw = d.get("temperature")
            h_raw = d.get("humidity")

            try:
                temp = float(t_raw) if t_raw not in ("", None) else None
            except Exception:
                temp = None

            try:
                hum = float(h_raw) if h_raw not in ("", None) else None
            except Exception:
                hum = None

            if temp is None and hum is None:
                # 両方空なら記録しない
                continue

            rows_by_sn[sn].append(
                {
                    "recorded_at": ts,
                    "temperature": temp,
                    "humidity": hum,
                }
            )

    # 時系列でソート
    for sn, rows in rows_by_sn.items():
        rows.sort(key=lambda r: r["recorded_at"])

    return rows_by_sn


@login_required
def history_7days(request: HttpRequest) -> JsonResponse:
    default_tz = timezone.get_default_timezone()
    now = timezone.localtime(timezone.now(), default_tz)

    # ★ 直近7日（今日を含む）の日付
    start_date = now.date() - timedelta(days=6)

    # ★ スロットは 7日 × 8本（00,03,06,09,12,15,18,21）で固定
    slot_hours = (0, 3, 6, 9, 12, 15, 18, 21)

    slots: list[datetime] = []
    for day_offset in range(7):
        d = start_date + timedelta(days=day_offset)
        for h in slot_hours:
            slots.append(
                timezone.make_aware(
                    datetime.combine(d, dt_time(hour=h, minute=0)),
                    default_tz,
                )
            )

    labels = [dt.strftime("%m/%d %H:%M") for dt in slots]

    # ★ DB・キャッシュ検索範囲
    start_dt = slots[0]
    end_dt = now  # ここは「今」まで。未来スロット分は None になる。

    result: dict[str, dict] = {}

    # 「倉庫」として扱うのは is_external=False のもの
    locations = Location.objects.filter(is_external=False).order_by("code")

    # 割当情報を取得
    assignments = (
        DeviceAssignment.objects
        .filter(location__in=locations)
        .select_related("location")
    )

    location_sns: dict[int, list[str]] = {}
    all_sns_set: set[str] = set()

    for a in assignments:
        if not a.location:
            continue
        sn = str(a.device_id)
        location_sns.setdefault(a.location.id, []).append(sn)
        all_sns_set.add(sn)

    # キャッシュから全SN分の履歴を一括読み込み
    cache_rows_by_sn = load_cache_history_for_sns(all_sns_set, start_dt, end_dt)

    for loc in locations:
        # このロケーションに割り当てられているデバイス一覧
        sns = location_sns.get(loc.id, [])
        if not sns:
            continue  # デバイスがない倉庫はスキップ

        # DB から履歴を取得
        qs = (
            DeviceHistory.objects
            .filter(sn__in=sns, recorded_at__gte=start_dt, recorded_at__lte=end_dt)
            .order_by("sn", "recorded_at")
        )

        # rows_by_sn[sn] = [{recorded_at, temperature, humidity}, ...]
        rows_by_sn: dict[str, list[dict]] = {sn: [] for sn in sns}

        # DBからの行を格納
        for row in qs:
            rows_by_sn[row.sn].append(
                {
                    "recorded_at": row.recorded_at,
                    "temperature": float(row.temperature) if row.temperature is not None else None,
                    "humidity": float(row.humidity) if row.humidity is not None else None,
                }
            )

        # キャッシュからの行をマージ
        for sn in sns:
            cache_rows = cache_rows_by_sn.get(sn, [])
            if cache_rows:
                rows_by_sn[sn].extend(cache_rows)

        # 時系列でソート
        for sn in sns:
            rows_by_sn[sn].sort(key=lambda r: r["recorded_at"])

        # SNごとにどこまで読んだかのポインタ
        index_by_sn: dict[str, int] = {sn: 0 for sn in sns}

        # デバイスごとのスロット別値
        device_series: dict[str, dict[str, list]] = {
            sn: {"temps": [], "hums": []} for sn in sns
        }

        # 倉庫平均用
        temps_series: list[float | None] = []
        hums_series: list[float | None] = []

        for i, slot_start in enumerate(slots):
            # このスロットの終了時刻（次スロットまで）
            if i + 1 < len(slots):
                slot_end = slots[i + 1]
            else:
                slot_end = end_dt + timedelta(seconds=1)

            slot_temps: list[float] = []
            slot_hums: list[float] = []

            for sn in sns:
                rows = rows_by_sn.get(sn, [])
                if not rows:
                    device_series[sn]["temps"].append(None)
                    device_series[sn]["hums"].append(None)
                    continue

                idx = index_by_sn.get(sn, 0)

                # slot_start 以前のデータをスキップ
                while idx < len(rows) and rows[idx]["recorded_at"] < slot_start:
                    idx += 1

                temp_val = None
                hum_val = None

                # このスロットで最初に現れたレコードが slot_end 未満なら採用
                if idx < len(rows):
                    row = rows[idx]
                    if row["recorded_at"] < slot_end:
                        if row["temperature"] is not None:
                            temp_val = float(row["temperature"])
                            slot_temps.append(temp_val)
                        if row["humidity"] is not None:
                            hum_val = float(row["humidity"])
                            slot_hums.append(hum_val)

                # デバイス別シリーズに追加（なければ None）
                device_series[sn]["temps"].append(temp_val)
                device_series[sn]["hums"].append(hum_val)

                # ポインタ更新
                index_by_sn[sn] = idx

            # 倉庫平均値
            if slot_temps:
                temps_series.append(round(sum(slot_temps) / len(slot_temps), 1))
            else:
                temps_series.append(None)

            if slot_hums:
                hums_series.append(round(sum(slot_hums) / len(slot_hums), 1))
            else:
                hums_series.append(None)

        # 少なくともどこか1スロットにデータがある倉庫だけ結果に載せる
        if any(v is not None for v in temps_series) or any(v is not None for v in hums_series):
            result[loc.name] = {
                "labels": labels,
                "temps": temps_series,   # 倉庫平均
                "hums": hums_series,
                "devices": [
                    {
                        "sn": sn,
                        "temps": device_series[sn]["temps"],
                        "hums": device_series[sn]["hums"],
                    }
                    for sn in sns
                ],
            }

    return JsonResponse(result, safe=True)


@login_required
@require_POST
def manual_fetch_history(request):
    """
    設定画面から「履歴データを手動取得」ボタンを押したときに呼ばれる。
    内部的には management command 'fetch_env_history' を実行する。
    """
    env = EnvSettings.get_solo()

    # すでに実行中ならスキップ（多重起動防止）
    if env.is_fetching_history:
        messages.warning(
            request,
            "履歴データ取得処理がすでに実行中のため、手動実行はスキップしました。"
        )
        return redirect("envmon:settings")

    try:
        # fetch_env_history 側で is_fetching_history フラグの ON/OFF を管理している
        call_command("fetch_env_history")
        messages.success(
            request,
            "履歴データの取得処理を実行しました。詳細はログを確認してください。"
        )
    except Exception as e:
        messages.error(
            request,
            f"履歴データ取得処理中にエラーが発生しました: {e}"
        )

    return redirect("envmon:settings")


@login_required
@require_POST
def manual_cache(request):
    """
    設定画面から「キャッシュを手動取得」ボタンを押したときに呼ばれる。
    外部 API から全デバイスのリアルタイムデータを取得して
    device_cache_latest.json を更新する。
    """
    env = EnvSettings.get_solo()

    # ★ 履歴取得中はキャッシュ取得を禁止（token 競合防止）
    if env.is_fetching_history:
        messages.warning(
            request,
            "現在、履歴データ取得中のためキャッシュ手動取得は実行できません。"
        )
        return redirect("envmon:settings")

    try:
        call_command("run_env_cache")
        messages.success(request, "キャッシュの手動取得を実行しました。")
    except Exception as e:
        messages.error(request, f"キャッシュ手動取得中にエラーが発生しました: {e}")

    return redirect("envmon:settings")
