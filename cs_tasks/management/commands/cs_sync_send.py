# cs_tasks/management/commands/cs_sync_send.py
"""往路: 同期スナップショットをMac宛にメール送信する。

例:
  python manage.py cs_sync_send            # 全件スナップショット
  python manage.py cs_sync_send --minutes 15   # 直近15分の差分のみ
  python manage.py cs_sync_send --dry-run      # 送信せず内容だけ確認
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils.timezone import localtime

from cs_tasks.bridge import outbound, payload


class Command(BaseCommand):
    help = "CS課題の同期スナップショットをMac宛にメール送信する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=None,
            help="直近N分に更新があった課題のみ送る(未指定なら全件)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="送信せずスナップショット本文を標準出力に表示",
        )

    def handle(self, *args, **options):
        since = None
        if options["minutes"]:
            since = localtime() - timedelta(minutes=options["minutes"])

        if options["dry_run"]:
            snapshot = outbound.build_snapshot(since=since)
            self.stdout.write(payload.wrap_sync(snapshot))
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] tasks={len(snapshot['tasks'])} seq={snapshot['seq']}"
                )
            )
            return

        res = outbound.send_snapshot(since=since)
        if res.get("sent"):
            self.stdout.write(
                self.style.SUCCESS(
                    f"送信完了 seq={res.get('seq')} tasks={res.get('task_count')}"
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(f"未送信: {res.get('reason')}")
            )
