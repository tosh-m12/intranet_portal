# envmon/management/commands/fetch_env_history.py

from django.core.management.base import BaseCommand
from django.db import transaction

from envmon.models import EnvSettings
from envmon.views import fetch_history_all_core


class Command(BaseCommand):
    help = "1weilian から全デバイスの履歴データを取得して DeviceHistory に保存する"

    def handle(self, *args, **options):
        # EnvSettings を1件取得
        env = EnvSettings.get_solo()

        # すでに実行中ならスキップ
        if env.is_fetching_history:
            self.stdout.write(self.style.WARNING(
                "[fetch_env_history] すでに履歴取得処理が実行中のためスキップします。"
            ))
            return

        # フラグを立てる
        try:
            with transaction.atomic():
                env.is_fetching_history = True
                env.save(update_fields=["is_fetching_history"])

            self.stdout.write("[fetch_env_history] 履歴取得処理を開始します。")

            # 実際の履歴取得処理
            total_new = fetch_history_all_core()

            self.stdout.write(self.style.SUCCESS(
                f"[fetch_env_history] 履歴取得処理が完了しました。（試行件数: {total_new}）"
            ))

        except Exception as e:
            self.stderr.write(self.style.ERROR(
                f"[fetch_env_history] エラー: {e}"
            ))
            raise

        finally:
            # フラグは必ず戻す（例外時も確実に）
            EnvSettings.objects.filter(pk=env.pk).update(is_fetching_history=False)
            self.stdout.write("[fetch_env_history] is_fetching_history を False に戻しました。")
