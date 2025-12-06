# envmon/cache_logger_dj.py
import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path

# ==== Django セットアップ ====
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intranet_portal.settings")

import django  # noqa: E402
django.setup()

from django.utils import timezone  # noqa: E402
from envmon.models import EnvSettings, EnvLog, DeviceAssignment  # noqa: E402

CACHE_DIR = BASE_DIR / "cache"


def find_best_cache_file(now: datetime) -> tuple[Path | None, datetime | None]:
    """
    device_cache_YYYYmmddHHMMSS.json の中から、
    now に最も近い「過去または現在」のキャッシュを選ぶ。
    1件もなければ None を返す。
    """
    candidates: list[tuple[datetime, Path]] = []

    if not CACHE_DIR.exists():
        return None, None

    for fname in os.listdir(CACHE_DIR):
        if not fname.startswith("device_cache_"):
            continue
        if fname == "device_cache_latest.json":
            # 特別ファイルは除外（必要なら fallback に使う）
            continue
        if not fname.endswith(".json"):
            continue

        ts_str = fname.replace("device_cache_", "").replace(".json", "")
        try:
            ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
        except Exception:
            continue

        path = CACHE_DIR / fname
        candidates.append((ts, path))

    if not candidates:
        # fallback: latest を使う（あれば）
        latest = CACHE_DIR / "device_cache_latest.json"
        if latest.exists():
            ts = datetime.fromtimestamp(latest.stat().st_mtime)
            return latest, ts
        return None, None

    # 時刻でソートして、now 以下で最大のものを探す
    candidates.sort(key=lambda x: x[0])  # ts 昇順
    chosen = None
    for ts, path in candidates:
        if ts <= now:
            chosen = (ts, path)
        else:
            break

    if chosen is None:
        # 全部 future の場合は最も古いものを使う
        chosen = candidates[0]

    return chosen[1], chosen[0]


def load_devices_from_cache(cache_path: Path) -> list[dict]:
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            devices = json.load(f)
        if not isinstance(devices, list):
            raise ValueError("cache json is not list")
        return devices
    except Exception as e:
        print(f"[ERROR] Failed to load cache {cache_path}: {e}")
        return []


def attach_assignments(devices: list[dict]) -> None:
    """
    cache_worker_dj と同様の考え方で、
    DB の DeviceAssignment から location と warehouse 名を付与する。
    """
    id_to_device: dict[str, dict] = {}
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
            dev["location_obj"] = loc   # 後で EnvLog に入れる
            dev["warehouse"] = loc.name
        else:
            dev["location_obj"] = None
            dev["warehouse"] = "未割当"

    # 割当のないデバイスにも明示的に "未割当" を入れる
    for dev_id, d in id_to_device.items():
        if "warehouse" not in d:
            d["location_obj"] = None
            d["warehouse"] = "未割当"


def parse_float(s):
    if s in ("", None):
        return None
    try:
        return float(s)
    except Exception:
        return None


def write_logs_to_db(log_ts: datetime, devices: list[dict]) -> int:
    """
    devices の内容を EnvLog に一括 INSERT。
    log_ts はキャッシュファイルのタイムスタンプを使用。
    戻り値は登録件数。
    """
    logs: list[EnvLog] = []
    # timezone-aware にしておく
    if timezone.is_naive(log_ts):
        log_ts = timezone.make_aware(log_ts, timezone.get_current_timezone())

    for d in devices:
        device_id = str(d.get("id"))
        temp = parse_float(d.get("temperature"))
        hum = parse_float(d.get("humidity"))
        online = bool(d.get("online"))

        loc_obj = d.get("location_obj")
        warehouse = d.get("warehouse") or ""

        logs.append(
            EnvLog(
                timestamp=log_ts,
                device_id=device_id,
                location=loc_obj,
                warehouse=warehouse,
                temperature=temp,
                humidity=hum,
                online=online,
            )
        )

    if not logs:
        return 0

    EnvLog.objects.bulk_create(logs)
    return len(logs)


def main():
    print("[DEBUG] cache_logger_dj started (DB logging mode)")
    last_logged: dict[str, datetime.date] = {}  # "HH:MM" -> date

    while True:
        now = timezone.now()
        env = EnvSettings.get_solo()
        log_times = env.log_times or []  # ["03:00", "09:00", ...]

        did_log = False

        # 現在の "HH:MM"
        now_key = now.strftime("%H:%M")
        today = now.date()

        for t in log_times:
            # その時刻ぴったりかつ、今日まだログを取っていないなら実行
            if now_key == t and last_logged.get(t) != today:
                print(f"[DEBUG] Time matched for log_time={t}, now={now}")
                cache_path, log_ts = find_best_cache_file(now)
                if not cache_path or not log_ts:
                    print("[WARN] No cache file found to log.")
                    last_logged[t] = today  # ない場合でも二重実行防止
                    continue

                print(f"[INFO] Using cache file {cache_path} (ts={log_ts}) for logging")

                devices = load_devices_from_cache(cache_path)
                attach_assignments(devices)
                count = write_logs_to_db(log_ts, devices)

                print(f"[INFO] Logged {count} rows into EnvLog at {log_ts} for time {t}")
                last_logged[t] = today
                did_log = True

        if not did_log:
            print("[DEBUG] Not time to log yet.")

        # 1分ごとに判定
        time.sleep(60)


if __name__ == "__main__":
    main()
