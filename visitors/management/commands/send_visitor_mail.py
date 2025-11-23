# visitors/management/commands/send_visitor_mail.py

from django.core.management.base import BaseCommand
import logging

from visitors.email_utils import send_daily_email

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "来訪予定のメールを1回送信する（Windowsタスクスケジューラ等から使用）"

    def handle(self, *args, **options):
        logger.info("[VISITOR_MAIL_CMD] send_visitor_mail start")
        try:
            result = send_daily_email()

            if result["sent"]:
                msg = (
                    f"メール送信完了: 宛先={len(result['recipients'])}件, "
                    f"来訪件数={result['visitor_count']}件"
                )
                self.stdout.write(self.style.SUCCESS(msg))
                logger.info(f"[VISITOR_MAIL_CMD] {msg} recipients={result['recipients']}")
            else:
                msg = f"メールは送信されませんでした: {result['reason']}"
                self.stdout.write(self.style.WARNING(msg))
                logger.warning(f"[VISITOR_MAIL_CMD] {msg} recipients={result['recipients']}")
        except Exception as e:
            msg = f"メール送信中にエラー: {e}"
            self.stderr.write(self.style.ERROR(msg))
            logger.error(f"[VISITOR_MAIL_CMD] EXCEPTION: {e}", exc_info=True)
            raise
