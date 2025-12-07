# envmon/management/commands/run_env_cache.py

import json
from datetime import datetime, timedelta
from pathlib import Path
import os

from django.core.management.base import BaseCommand
from django.utils import timezone

from envmon.models import EnvSettings
from envmon.views import (
    get_device_api_token,
    fetch_all_devices,
    attach_assignments,
    CACHE_DIR,
)


class Command(BaseCommand):
    help = "Fetch realtime envmon data from external API and save JSON cache (once)."

    def handle(self, *args, **options):
        self.stdout.write("[run_env_cache] start")

        # ==== 1. 設定読み込み ====
        env = EnvSettings.get_solo()
        expire_hours = env.cache_expire_hours or 720  # デフォルト 30日

        # キャッシュディレクトリ確保
        CACHE_DIR.mkdir(exist_ok=True)
        self.stdout.write(f"[run_env_cache] CACHE_DIR = {CACHE_DIR}")

        # ==== 2. 外部APIからリアルタイム全デバイス取得 ====
        try:
            token, user_id = get_device_api_token()
            devices = fetch_all_devices(token, user_id)
        except Exception as e:
            self.stderr.write(f"[run_env_cache] error while fetching devices: {e}")
            return

        if devices is None:
            devices = []
        self.stdout.write(f"[run_env_cache] fetched devices count = {len(devices)}")

        # ==== 3. 倉庫割当情報を付与 ====
        try:
            attach_assignments(devices)
        except Exception as e:
            self.stderr.write(f"[run_env_cache] error in attach_assignments: {e}")

        # ==== 4. キャッシュ保存 ====
        now = timezone.localtime(timezone.now())
        ts_str = now.strftime("%Y%m%d%H%M%S")

        cache_file = CACHE_DIR / f"device_cache_{ts_str}.json"
        latest_cache_file = CACHE_DIR / "device_cache_latest.json"

        try:
            with cache_file.open("w", encoding="utf-8") as f:
                json.dump(devices, f, ensure_ascii=False, indent=2)
            self.stdout.write(f"[run_env_cache] saved cache: {cache_file}")

            with latest_cache_file.open("w", encoding="utf-8") as f:
                json.dump(devices, f, ensure_ascii=False, indent=2)
            self.stdout.write(f"[run_env_cache] updated latest cache: {latest_cache_file}")
        except Exception as e:
            self.stderr.write(f"[run_env_cache] error while saving cache files: {e}")
            return

        # ==== 5. 古いキャッシュ削除 ====
        try:
            # 時間比較用に naive に揃える
            now_naive = now.replace(tzinfo=None)
            cutoff = now_naive - timedelta(hours=expire_hours)

            for path in CACHE_DIR.glob("device_cache_*.json"):
                # latest は削除しない
                if path.name == "device_cache_latest.json":
                    continue

                try:
                    # ファイル名からタイムスタンプ部分だけ抜き出す
                    name = path.name  # 例: device_cache_20251207123345.json
                    ts_part = name.replace("device_cache_", "").replace(".json", "")
                    file_dt = datetime.strptime(ts_part, "%Y%m%d%H%M%S")
                except Exception:
                    # 想定外の名前はスキップ
                    continue

                if file_dt < cutoff:
                    try:
                        os.remove(path)
                        self.stdout.write(f"[run_env_cache] deleted old cache: {path}")
                    except Exception as e:
                        self.stderr.write(f"[run_env_cache] failed to delete {path}: {e}")

        except Exception as e:
            self.stderr.write(f"[run_env_cache] error while cleaning cache: {e}")

        self.stdout.write(self.style.SUCCESS("[run_env_cache] done"))
