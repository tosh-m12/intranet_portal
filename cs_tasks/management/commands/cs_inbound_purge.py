# cs_tasks/management/commands/cs_inbound_purge.py
"""メール経路撤去に伴う「溜まったブリッジメールの一括掃除」（一回限り）。

連携は HTTP API に一本化済み。過去にメールで往復していた頃のブリッジメールを掃除する:
  - 受信箱(INBOX) の [CS-WB]  … Mac から届いた書き戻しメール
  - 送信箱(Sent)   の [CS-SYNC]… 本番から送っていた同期メール（保存されている場合のみ）

安全策:
  - 件名に CS-WB / CS-SYNC マーカーがある事を確認してから削除（無関係メールを消さない）。
  - --dry-run で件数だけ確認可能。
  - 受信箱の [CS-WB] は既定で SEEN(処理済み)のみ。--include-unseen で未読も対象。
  - 送信箱が見つからなければスキップ（best-effort）。

資格情報:
  1. CS_BRIDGE_INTAKE_IMAP_*（あれば優先）
  2. 無ければ mailcenter.MailAccount（来客通知などの送信アカウント=cs_info）から流用。
     IMAP host は SMTP host から導出（smtp.* → imap.*）、SSL 993。
"""
import imaplib

from django.conf import settings
from django.core.management.base import BaseCommand


def _resolve_imap():
    """(host, port, user, password, use_ssl) を返す。解決できなければ None。"""
    host = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_HOST", "") or ""
    user = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_USER", "") or ""
    password = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_PASSWORD", "") or ""
    if host and user and password:
        return (
            host,
            getattr(settings, "CS_BRIDGE_INTAKE_IMAP_PORT", 993),
            user, password,
            getattr(settings, "CS_BRIDGE_INTAKE_IMAP_SSL", True),
        )

    # フォールバック: 送信用 MailAccount(cs_info)の資格情報を IMAP に流用する。
    try:
        from mailcenter.models import MailAccount
    except Exception:
        return None
    acct = (
        MailAccount.objects.filter(smtp_user="cs_info@ngls.sh.cn").first()
        or MailAccount.objects.exclude(smtp_user="").first()
        or MailAccount.objects.first()
    )
    if not acct or not (acct.smtp_user and acct.smtp_password):
        return None
    smtp_host = (acct.smtp_host or "smtp.qiye.aliyun.com").strip()
    imap_host = ("imap." + smtp_host[len("smtp."):]) if smtp_host.startswith("smtp.") else smtp_host
    return (imap_host, 993, acct.smtp_user.strip(), acct.smtp_password.strip(), True)


class Command(BaseCommand):
    help = "溜まったブリッジメール([CS-WB]受信 / [CS-SYNC]送信済)を一括削除する（過去分の掃除）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="削除せず対象件数だけ表示する。",
        )
        parser.add_argument(
            "--include-unseen", action="store_true",
            help="受信箱の未読(未処理) [CS-WB] も対象に含める（既定は処理済みSEENのみ）。",
        )

    # ----------------------------------------------------------------- helpers
    def _purge(self, server, mailbox, marker, seen_only, dry_run):
        """指定 mailbox 内の、件名に marker を含むメールを削除する。(対象数, 削除数)。"""
        typ, _ = server.select(mailbox)
        if typ != "OK":
            return (0, 0)
        # IMAP SUBJECT 検索は部分一致。SEEN 条件は受信箱の処理済みだけに絞る用。
        query = f'(SEEN SUBJECT "{marker}")' if seen_only else f'(SUBJECT "{marker}")'
        typ, data = server.search(None, query)
        if typ != "OK" or not data or data[0] is None:
            return (0, 0)
        ids = data[0].split()

        to_delete = []
        for num in ids:
            # 件名ヘッダだけ PEEK 取得してマーカーを再確認（フラグは変えない）
            typ, md = server.fetch(num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
            if typ != "OK" or not md or not md[0]:
                continue
            header = (md[0][1] or b"").decode("utf-8", errors="replace")
            if marker not in header:
                continue  # 念のため無関係メールは除外
            to_delete.append(num)

        if dry_run or not to_delete:
            return (len(to_delete), 0)
        for num in to_delete:
            server.store(num, "+FLAGS", "\\Deleted")
        server.expunge()
        return (len(to_delete), len(to_delete))

    def _sent_mailboxes(self, server):
        """送信箱とみなせるフォルダ名の一覧（\\Sent 特殊用途 or 名称一致）。"""
        found = []
        typ, lines = server.list()
        if typ != "OK" or not lines:
            return found
        for raw in lines:
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            low = line.lower()
            is_sent = ("\\sent" in low) or ("sent" in low) or ("已发送" in line) or ("已發送" in line)
            if not is_sent:
                continue
            # 行末の引用付きフォルダ名を取り出す（'... "Sent Messages"' / '... INBOX/Sent'）
            name = line.split(' "')[-1].rstrip('"') if ' "' in line else line.split()[-1]
            if name and name not in found:
                found.append(name)
        return found

    # ----------------------------------------------------------------- handle
    def handle(self, *args, **options):
        creds = _resolve_imap()
        if not creds:
            self.stdout.write(self.style.WARNING(
                "IMAP 資格情報が解決できないためスキップします（CS_BRIDGE_INTAKE_IMAP_* / MailAccount いずれも未設定）。"))
            return
        host, port, user, password, use_ssl = creds
        mailbox = getattr(settings, "CS_BRIDGE_INTAKE_MAILBOX", "INBOX")
        dry_run = options.get("dry_run")
        include_unseen = options.get("include_unseen")

        server = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        total_target = 0
        total_deleted = 0
        try:
            server.login(user, password)

            # 1) 受信箱の [CS-WB]
            t, d = self._purge(server, mailbox, "CS-WB", seen_only=not include_unseen, dry_run=dry_run)
            total_target += t
            total_deleted += d
            self.stdout.write(f"受信箱 [CS-WB]: 対象 {t} 件 / 削除 {d} 件")

            # 2) 送信箱の [CS-SYNC]（あれば。送信メールに SEEN/UNSEEN は使わない）
            for sent in self._sent_mailboxes(server):
                try:
                    t, d = self._purge(server, sent, "CS-SYNC", seen_only=False, dry_run=dry_run)
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"送信箱 {sent!r} の処理をスキップ: {e}"))
                    continue
                if t:
                    total_target += t
                    total_deleted += d
                    self.stdout.write(f"送信箱 {sent!r} [CS-SYNC]: 対象 {t} 件 / 削除 {d} 件")
        finally:
            try:
                server.logout()
            except Exception:
                pass

        if dry_run:
            self.stdout.write(self.style.WARNING(f"--dry-run のため削除しません（対象合計 {total_target} 件）。"))
        else:
            self.stdout.write(self.style.SUCCESS(f"削除合計: {total_deleted} 件"))
