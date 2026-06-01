# cs_tasks/management/commands/cs_inbound_poll.py
"""復路: 専用受信箱(IMAP)を監視し、書き戻しメールをDBへ反映する。

設定(settings / 環境変数):
  CS_BRIDGE_INTAKE_IMAP_HOST / _PORT / _USER / _PASSWORD / _SSL
  CS_BRIDGE_INTAKE_MAILBOX (既定 'INBOX')

未設定なら安全にスキップする。検証(差出人限定・HMAC・冪等)は
cs_tasks.bridge.inbound 側で行う。
"""
import email
import imaplib
from email.header import decode_header, make_header

from django.conf import settings
from django.core.management.base import BaseCommand

from cs_tasks.bridge import inbound


def _decode(value):
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _plain_text_body(msg):
    """email.message.Message から text/plain 本文を取り出す。"""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


class Command(BaseCommand):
    help = "書き戻しメール(IMAP)を取得してDBへ反映する"

    def handle(self, *args, **options):
        host = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_HOST", "") or ""
        user = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_USER", "") or ""
        password = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_PASSWORD", "") or ""
        if not (host and user and password):
            self.stdout.write(
                self.style.WARNING("受信箱(IMAP)が未設定のためスキップします。")
            )
            return

        port = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_PORT", 993)
        use_ssl = getattr(settings, "CS_BRIDGE_INTAKE_IMAP_SSL", True)
        mailbox = getattr(settings, "CS_BRIDGE_INTAKE_MAILBOX", "INBOX")

        if use_ssl:
            server = imaplib.IMAP4_SSL(host, port)
        else:
            server = imaplib.IMAP4(host, port)

        processed = 0
        try:
            server.login(user, password)
            server.select(mailbox)
            typ, data = server.search(None, "UNSEEN")
            if typ != "OK":
                self.stdout.write(self.style.ERROR("IMAP search に失敗。"))
                return

            for num in data[0].split():
                typ, msg_data = server.fetch(num, "(RFC822)")
                if typ != "OK":
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                sender = _decode(msg.get("From"))
                body = _plain_text_body(msg)

                result = inbound.apply_writeback_text(body, sender=sender)
                if result.get("ok"):
                    server.store(num, "+FLAGS", "\\Seen")
                    processed += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"適用: applied={len(result['applied'])} "
                            f"skipped={len(result['skipped'])} "
                            f"errors={len(result['errors'])} ({result.get('reason') or 'ok'})"
                        )
                    )
                else:
                    # 検証失敗は既読にせず残す(誤判定時の手動確認のため)
                    self.stdout.write(
                        self.style.ERROR(f"拒否: {result.get('reason')} from={sender!r}")
                    )
        finally:
            try:
                server.logout()
            except Exception:
                pass

        self.stdout.write(self.style.SUCCESS(f"処理メール数: {processed}"))
