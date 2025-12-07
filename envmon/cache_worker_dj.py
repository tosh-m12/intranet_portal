# envmon/cache_worker_dj.py
import os
import sys
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
import urllib.parse

import requests

API_TIMEOUT = 30

# ==== プロジェクトルートを sys.path に追加 ====
# このファイル: .../intranet_portal/envmon/cache_worker_dj.py
# プロジェクトルート: .../intranet_portal
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# ==== Django セットアップ ====
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intranet_portal.settings")

import django  # noqa: E402
django.setup()

from django.conf import settings  # noqa: E402
from envmon.models import EnvSettings, DeviceAssignment, Location  # noqa: E402

# ==== 外部 API 定数（Flask 版から移植） ====
LOGIN_URL = "https://1weilian.com/user/login"          # ← そのままでもOK（301で www に飛ばしている）
DATA_URL  = "https://www.1weilian.com/public/realTimeData"
ACCOUNT = "nglswhs47"
PASSWORD = "ngls1234"

# settings.BASE_DIR ではなく、上で計算した BASE_DIR を使う
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
print(f"[INFO] Cache directory ensured at: {CACHE_DIR}")


def get_env_settings() -> EnvSettings:
    return EnvSettings.get_solo()


def login_and_get_token():
    payload = {
        "account": ACCOUNT,
        "pwd": PASSWORD,
        "systemVersion": "PC",
        "loginType": 2,
    }
    headers = {"Content-Type": "application/json;charset=UTF-8"}

    # まずはリダイレクトを追わずに投げる
    resp = requests.post(LOGIN_URL, json=payload, headers=headers, timeout=API_TIMEOUT, allow_redirects=False)

    # 30x（リダイレクト）の場合は、自分で再度 POST する
    if resp.status_code in (301, 302, 303, 307, 308):
        loc = resp.headers.get("Location")
        if not loc:
            raise RuntimeError(f"Login redirect without Location header: {resp.status_code}")

        # 相対URLの場合もあるのでフルURLにする
        login_url2 = urllib.parse.urljoin(LOGIN_URL, loc)
        print(f"[LOGIN-DEBUG] redirect {resp.status_code} -> {login_url2}")

        resp = requests.post(login_url2, json=payload, headers=headers, timeout=API_TIMEOUT, allow_redirects=False)

    # ここで最終レスポンスを確認
    try:
        result = resp.json()
    except ValueError:
        print("[LOGIN-DEBUG] 非JSONレスポンス:", resp.status_code, resp.text[:500])
        raise RuntimeError("Login API returned non-JSON response")

    print("[LOGIN-DEBUG] status:", resp.status_code)
    print("[LOGIN-DEBUG] body:", json.dumps(result, ensure_ascii=False)[:500])

    if "data" not in result:
        raise RuntimeError(f"Login API error. 'data' key not found. response={result}")

    return result["data"]["accessToken"], result["data"]["userId"]


def fetch_all_devices(token, user_id):
    """
    外部 API から全デバイスを取得。
    途中のページでタイムアウトしても、それまでに取得できた分は返す。
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
            # まずはリダイレクト追跡なしで POST
            resp = requests.post(
                DATA_URL,
                json=payload,
                headers=headers,
                timeout=API_TIMEOUT,
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
                    timeout=API_TIMEOUT,
                    allow_redirects=False,
                )

            # JSON に変換
            try:
                result = resp.json()
            except ValueError:
                print("[FETCH-DEBUG] 非JSONレスポンス:", resp.status_code, resp.text[:500])
                break

            # 1ページ目だけ中身を出力
            if page == 0:
                print("[FETCH-DEBUG] status:", resp.status_code)
                print("[FETCH-DEBUG] body:", json.dumps(result, ensure_ascii=False)[:500])

            # 想定通りの形式か確認
            if "data" not in result or "dataList" not in result["data"]:
                print("[ERROR] Unexpected device API response (no data.dataList):", result)
                break

            data_list = result["data"]["dataList"]
            if not data_list:
                # データが空ならここで終了
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

            # 次のページへ
            page += 1

        except requests.Timeout as e:
            print(f"[ERROR] Timeout when fetching page {page}: {e}")
            print("[INFO] Using partial device list so far. count =", len(all_devices))
            break
        except Exception as e:
            print(f"[ERROR] Fetch devices page {page} failed: {e}")
            print("[INFO] Using partial device list so far. count =", len(all_devices))
            break

    # ★ ここが重要：どんな経路でも必ず list を返す
    return all_devices


def attach_assignments(devices):
    id_to_device = {}
    for d in devices:
        dev_id = str(d.get("id"))
        id_to_device[dev_id] = d

    if not id_to_device:
        return

    assignments = (
        DeviceAssignment.objects.select_related("location")
        .filter(device_id__in=id_to_device.keys())
    )

    for a in assignments:
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

    for dev_id, dev in id_to_device.items():
        if "warehouse" not in dev:
            dev["location_id"] = None
            dev["warehouse"] = "未割当"


def cleanup_cache(expire_hours: int):
    now = datetime.now()
    for fname in os.listdir(CACHE_DIR):
        if not (fname.endswith(".json") and fname.startswith("device_cache_")):
            continue
        if fname == "device_cache_latest.json":
            continue

        try:
            ts_str = fname.replace("device_cache_", "").replace(".json", "")
            ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
        except Exception:
            continue

        if now - ts > timedelta(hours=expire_hours):
            path = CACHE_DIR / fname
            try:
                os.remove(path)
                print(f"[INFO] Deleted expired cache: {path}")
            except Exception as e:
                print(f"[ERROR] Failed to delete cache {path}: {e}")


def main(once: bool = False):
    print(f"[DEBUG] cache_worker_dj main loop starting (once={once})")

    # 設定ファイルが読めなくても最低限動くよう、デフォルト値を持っておく
    interval = 300  # デフォルト：5分
    expire_hours = 720

    while True:
        try:
            # ==== 1. Django側の設定（EnvSettings）を読み込む ====
            try:
                env = get_env_settings()  # EnvSettings.get_solo()
                if getattr(env, "cache_interval", None):
                    interval = env.cache_interval
                if getattr(env, "cache_expire_hours", None):
                    expire_hours = env.cache_expire_hours
            except Exception as e:
                print(f"[WARN] get_env_settings failed, using defaults. error={e}")

            # ==== 2. ログイン ====
            try:
                token, user_id = login_and_get_token()
            except Exception as e:
                print(f"[ERROR] Login failed: {e}")
                if once:
                    # 1回モードならここで終了
                    return
                time.sleep(interval)
                continue

            # ==== 3. デバイス取得 ====
            devices = fetch_all_devices(token, user_id)
            if devices is None:
                print("[WARN] fetch_all_devices returned None, treating as empty list")
                devices = []
            print(f"[DEBUG] fetched devices count = {len(devices)}")

            # ==== 4. 倉庫情報（DeviceAssignment / Location）を付与 ====
            attach_assignments(devices)

            # ==== 5. キャッシュ保存 ====
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            cache_file = CACHE_DIR / f"device_cache_{timestamp}.json"
            try:
                # デバッグ用にフルパスを表示
                print("### CACHE PATH =", os.path.abspath(str(cache_file)))
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(devices, f, ensure_ascii=False, indent=2)
                print(f"[INFO] Cache saved to {cache_file}")

                latest_cache_file = CACHE_DIR / "device_cache_latest.json"
                print("### LATEST CACHE PATH =", os.path.abspath(str(latest_cache_file)))
                with open(latest_cache_file, "w", encoding="utf-8") as f:
                    json.dump(devices, f, ensure_ascii=False, indent=2)
                print(f"[INFO] device_cache_latest.json updated")

            except Exception as e:
                print(f"[ERROR] Saving cache failed: {e}")

            # ==== 6. 古いキャッシュ削除 ====
            try:
                cleanup_cache(expire_hours)
            except Exception as e:
                print(f"[ERROR] Cache cleanup failed: {e}")

        except Exception as e:
            print(f"[FATAL ERROR] Unexpected error in main loop: {e}")
            import traceback
            traceback.print_exc()

        # ★ once=True の場合は 1サイクルで終了
        if once:
            break

        # ループ最後にインターバル分 sleep
        time.sleep(interval)


if __name__ == "__main__":
    import sys
    # コマンドライン引数に "--once" があれば 1回だけ実行
    once_mode = ("--once" in sys.argv)
    main(once=once_mode)
