# envmon/management/commands/import_envmon_initial_data.py
import json
from pathlib import Path
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from envmon.models import Location, DeviceAssignment, AssignmentHistory, EnvSettings


class Command(BaseCommand):
    help = "envmon 用の初期データを JSON から DB に取り込む"

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir",
            type=str,
            default="envmon/data",
            help="settings.json / locations.json / device_assignments.json / assignment_history.json が置いてあるディレクトリ（BASE_DIR からの相対パス or 絶対パス）",
        )

    def handle(self, *args, **options):
        data_dir = Path(options["data_dir"])
        if not data_dir.is_absolute():
            data_dir = Path(settings.BASE_DIR) / data_dir

        self.stdout.write(self.style.NOTICE(f"データディレクトリ: {data_dir}"))

        if not data_dir.exists():
            raise CommandError(f"data_dir が存在しません: {data_dir}")

        # ファイルパス
        settings_path = data_dir / "settings.json"
        locations_path = data_dir / "locations.json"
        assignments_path = data_dir / "device_assignments.json"
        history_path = data_dir / "assignment_history.json"

        # --- settings.json ---
        if settings_path.exists():
            self.stdout.write("settings.json を取り込み中...")
            with settings_path.open("r", encoding="utf-8") as f:
                s = json.load(f)

            env = EnvSettings.get_solo()
            env.interval = s.get("interval", env.interval)
            env.cache_interval = s.get("cache_interval", env.cache_interval)
            env.cache_expire_hours = s.get("cache_expire_hours", env.cache_expire_hours)
            env.log_directory = s.get("log_directory", env.log_directory)
            env.log_times = s.get("log_times", env.log_times)
            env.save()
            self.stdout.write(self.style.SUCCESS("settings.json の取り込み完了"))
        else:
            self.stdout.write(self.style.WARNING("settings.json が見つかりません。EnvSettings は既定値のままです。"))

        # --- locations.json ---
        if locations_path.exists():
            self.stdout.write("locations.json を取り込み中...")
            with locations_path.open("r", encoding="utf-8") as f:
                locations_data = json.load(f)

            for code, name in locations_data.items():
                is_external = code.startswith("external_")
                loc, created = Location.objects.get_or_create(
                    code=code,
                    defaults={
                        "name": name,
                        "is_external": is_external,
                    },
                )
                if not created:
                    # 既に存在する場合は名称のみ更新
                    loc.name = name
                    loc.is_external = is_external
                    loc.save()

            self.stdout.write(self.style.SUCCESS("locations.json の取り込み完了"))
        else:
            self.stdout.write(self.style.WARNING("locations.json が見つかりません。Location は作成されません。"))

        # --- device_assignments.json ---
        if assignments_path.exists():
            self.stdout.write("device_assignments.json を取り込み中...")
            with assignments_path.open("r", encoding="utf-8") as f:
                assignments_data = json.load(f)

            for device_id, loc_code in assignments_data.items():
                location = None
                if loc_code:
                    location = Location.objects.filter(code=loc_code).first()
                    if not location:
                        self.stdout.write(
                            self.style.WARNING(f"Location(code={loc_code}) が見つからないため、"
                                               f"device_id={device_id} は location=None で登録します。")
                        )

                DeviceAssignment.objects.update_or_create(
                    device_id=device_id,
                    defaults={
                        "location": location,
                    },
                )

            self.stdout.write(self.style.SUCCESS("device_assignments.json の取り込み完了"))
        else:
            self.stdout.write(self.style.WARNING("device_assignments.json が見つかりません。DeviceAssignment は作成されません。"))

        # --- assignment_history.json ---
        if history_path.exists():
            self.stdout.write("assignment_history.json を取り込み中...")
            with history_path.open("r", encoding="utf-8") as f:
                history_data = json.load(f)

            count = 0
            for device_id, records in history_data.items():
                for record in records:
                    loc_code = record.get("location_id")
                    ts_str = record.get("timestamp")
                    if not ts_str:
                        continue
                    try:
                        changed_at = datetime.fromisoformat(ts_str)
                    except ValueError:
                        self.stdout.write(self.style.WARNING(
                            f"timestamp 解析に失敗: {ts_str} (device_id={device_id})"
                        ))
                        continue

                    location = None
                    if loc_code:
                        location = Location.objects.filter(code=loc_code).first()

                    AssignmentHistory.objects.create(
                        device_id=device_id,
                        location=location,
                        changed_at=changed_at,
                    )
                    count += 1

            self.stdout.write(self.style.SUCCESS(f"assignment_history.json の取り込み完了 ({count} レコード)"))
        else:
            self.stdout.write(self.style.WARNING("assignment_history.json が見つかりません。AssignmentHistory は作成されません。"))

        self.stdout.write(self.style.SUCCESS("envmon 用の初期データ取り込みが完了しました。"))
