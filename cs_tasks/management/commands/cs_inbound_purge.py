# cs_tasks/management/commands/cs_inbound_purge.py
"""復路: 受信箱に溜まった処理済みの [CS-WB] メールを一括削除する（一回限りの掃除）。

通常の cs_inbound_poll は UNSEEN のみ処理して成功分を削除するため、本コマンド導入より
前に「既読(SEEN)のまま残った過去分」は溜まったまま。これを掃除する。

安全策:
  - 既定は SEEN（=処理済み）の [CS-WB] のみ対象。未読(UNSEEN=未処理)は触らない。
  - 件名に CS-WB マーカーがある事を確認してから削除（無関係メールを消さない）。
  - --dry-run で件数だけ確認可能。
  - 過去分の原文はDB保存しない方針（これ以降の受信は cs_inbound_poll が保存してから削除）。

設定は cs_inbound_poll と同じ（CS_BRIDGE_INTAKE_IMAP_*）。未設定なら安全にスキップ。
"""
import imaplib

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "受信箱の処理済み [CS-WB] メールを一括削除する（過去分の掃除）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="削除せず対象件数だけ表示する。",
        )
        parser.add_argument(
            "--include-unseen", action="store_true",
            help="未読(未処理)の [CS-WB] も対象に含める（既定は処理済みSEENのみ）。",
        )

    def handle(self, *args, **options):
        host = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_HOST", "") or ""
        user = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_USER", "") or ""
        password = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_PASSWORD", "") or ""
        if not (host and user and password):
            self.stdout.write(self.style.WARNING("受信箱(IMAP)が未設定のためスキップします。"))
            return

        port = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_PORT", 993)
        use_ssl = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_SSL", True)
        mailbox = getattr(settings, "CS_BRIDGE_INTAKE_MAILBOX", "INBOX")
        dry_run = options.get("dry_run")
        include_unseen = options.get("include_unseen")

        server = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        deleted = 0
        try:
            server.login(user, password)
            server.select(mailbox)
            # 処理済み(SEEN)の [CS-WB] を対象に。--include-unseen で全 [CS-WB]。
            query = '(SUBJECT "CS-WB")' if include_unseen else '(SEEN SUBJECT "CS-WB")'
            typ, data = server.search(None, query)
            if typ != "OK":
                self.stdout.write(self.style.ERROR("IMAP search に失敗。"))
                return
            ids = data[0].split()

            to_delete = []
            for num in ids:
                # 件名ヘッダだけ取得して CS-WB マーカーを確認（フラグは変えない=PEEK）
                typ, md = server.fetch(num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                if typ != "OK" or not md or not md[0]:
                    continue
                header = (md[0][1] or b"").decode("utf-8", errors="replace")
                if "CS-WB" not in header:
                    continue  # 念のため無関係メールは除外
                to_delete.append(num)

            self.stdout.write(f"対象(処理済み [CS-WB]): {len(to_delete)} 件")
            if dry_run:
                self.stdout.write(self.style.WARNING("--dry-run のため削除しません。"))
                return

            if to_delete:
                for num in to_delete:
                    server.store(num, "+FLAGS", "\\Deleted")
                server.expunge()
                deleted = len(to_delete)
        finally:
            try:
                server.logout()
            except Exception:
                pass

        self.stdout.write(self.style.SUCCESS(f"削除: {deleted} 件"))
