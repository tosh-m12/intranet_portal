# cs_tasks/scheduler.py
import os
import subprocess
import threading
import time
import datetime
import logging

from django.core.management import call_command
from django.utils import timezone
from django.db import close_old_connections

logger = logging.getLogger(__name__)

_scheduler_started = False  # 多重起動防止

# CS Bridge: 往路/復路の実行間隔と最終実行時刻(プロセス内モノトニック秒)
_BRIDGE_INTERVAL_SEC = 300  # 5分
_last_sync_at = None
_last_inbound_at = None

# 自動デプロイ(Gitee 監視): upstream に新コミットがあれば pull → migrate →
# self-exit。run_portal.bat の loop が新コードで waitress を再起動する。
_DEPLOY_INTERVAL_SEC = 300       # 5分毎にチェック
_DEPLOY_COOLDOWN_SEC = 600       # 直前デプロイから10分は再デプロイしない
_DEPLOY_DISABLE_FLAG = r"D:\INTRANET_PORTAL\.no_auto_deploy"  # 緊急停止フラグ
_last_deploy_check_at = None
_last_deploy_at = None


def _project_root():
    """manage.py が居るディレクトリ(= git リポジトリのルート)を返す。"""
    here = os.path.abspath(__file__)  # cs_tasks/scheduler.py
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def _git(args, cwd, timeout=30):
    """git コマンドを実行し、stdout(strip)を返す。失敗時は None。"""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        if result.returncode != 0:
            logger.warning(
                "[AUTO_DEPLOY] git %s rc=%d stderr=%s",
                args, result.returncode, (result.stderr or "").strip(),
            )
            return None
        return (result.stdout or "").strip()
    except subprocess.TimeoutExpired:
        logger.warning("[AUTO_DEPLOY] git %s timeout", args)
        return None
    except FileNotFoundError:
        logger.warning("[AUTO_DEPLOY] git not found on PATH")
        return None
    except Exception:
        logger.exception("[AUTO_DEPLOY] git %s exception", args)
        return None


def _auto_deploy_check():
    """upstream に新コミットがあれば pull + migrate + self-exit する。

    self-exit 後は run_portal.bat の loop が新コードで waitress を再起動する想定。
    pull や migrate に失敗した場合は exit せず、ログのみ残す(=現行コード継続)。
    """
    global _last_deploy_at

    # 緊急停止フラグ(touch しておけばデプロイを一時停止できる)
    if os.path.exists(_DEPLOY_DISABLE_FLAG):
        return

    # 直前デプロイから 10 分以内は何もしない
    if _last_deploy_at is not None and (time.monotonic() - _last_deploy_at) < _DEPLOY_COOLDOWN_SEC:
        return

    cwd = _project_root()

    # 現在のブランチ(detached HEAD は除外)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if not branch or branch == "HEAD":
        return

    if _git(["fetch", "--quiet"], cwd, timeout=30) is None:
        return

    local = _git(["rev-parse", "HEAD"], cwd)
    upstream = _git(["rev-parse", "@{u}"], cwd)
    if not local or not upstream or local == upstream:
        return

    logger.info(
        "[AUTO_DEPLOY] new commits on %s: %s -> %s",
        branch, local[:8], upstream[:8],
    )
    print(f"### [AUTO_DEPLOY] new commits on {branch}: {local[:8]} -> {upstream[:8]}")

    # fast-forward only で安全に pull (conflict は手動介入)
    if _git(["pull", "--ff-only"], cwd, timeout=60) is None:
        logger.error("[AUTO_DEPLOY] pull failed, abort restart")
        return

    # 新マイグレーション適用(無ければ no-op)
    try:
        call_command("migrate", "--noinput")
    except Exception:
        logger.exception("[AUTO_DEPLOY] migrate failed, abort restart")
        return

    _last_deploy_at = time.monotonic()
    logger.warning(
        "[AUTO_DEPLOY] code updated to %s, exiting in 3s for restart by run_portal.bat",
        upstream[:8],
    )
    print(f"### [AUTO_DEPLOY] code updated to {upstream[:8]}, exiting in 3s")

    # 進行中リクエストを多少待ってから self-exit
    def _exit_soon():
        time.sleep(3)
        os._exit(0)
    threading.Thread(target=_exit_soon, daemon=True).start()


def _scheduler_loop():
    """
    60秒ごとに tick し、
    - mode == django
    - 今日が送信曜日
    - 送信時刻を過ぎている
    - 今日まだ送っていない（last_sent_date != today）
    を満たしたら週報を送信する。
    """
    logger.info("[CSTASKS_SCHED] scheduler loop start")
    print("### cs_tasks scheduler loop start")
    tz = timezone.get_current_timezone()

    while True:
        try:
            from .models import WeeklyReportConfig
            from .email_utils import send_weekly_report

            now = timezone.localtime()
            today = now.date()

            config, _ = WeeklyReportConfig.objects.get_or_create(pk=1)

            if config.mode != WeeklyReportConfig.MODE_DJANGO:
                # 自動送信なし
                pass
            else:
                send_time = config.send_time or datetime.time(18, 0)
                scheduled_dt = timezone.make_aware(
                    datetime.datetime.combine(today, send_time), tz
                )

                logger.info(
                    "[CSTASKS_SCHED] tick now=%s, weekday=%s(target=%s), "
                    "send_time=%s, last_sent_date=%s",
                    now, now.weekday(), config.send_weekday,
                    send_time, config.last_sent_date,
                )

                if (
                    now.weekday() == config.send_weekday
                    and now >= scheduled_dt
                    and config.last_sent_date != today
                ):
                    logger.info("[CSTASKS_SCHED] conditions met, sending weekly report")
                    res = send_weekly_report(ignore_schedule=False)
                    if res.get("sent"):
                        config.last_sent_date = today
                        config.save(update_fields=["last_sent_date"])
                        logger.info(
                            "[CSTASKS_SCHED] sent, last_sent_date=%s", today
                        )
                    else:
                        logger.warning(
                            "[CSTASKS_SCHED] NOT sent (reason=%s)", res.get("reason")
                        )

            # ===== CS Bridge: 5分毎に 往路/復路 を Waitress プロセス内で実行 =====
            # タスクスケジューラ起動の .bat に頼らず、Waitress 内スレッドで定期実行する。
            # 環境変数(CS_BRIDGE_*)は run_portal.bat 経由で Waitress プロセスに継承済み。
            global _last_sync_at, _last_inbound_at
            mono = time.monotonic()
            if _last_sync_at is None or (mono - _last_sync_at) >= _BRIDGE_INTERVAL_SEC:
                try:
                    print("### [CSBRIDGE_SCHED] running cs_sync_send")
                    call_command("cs_sync_send", "--minutes", "30")
                except Exception:
                    logger.exception("[CSBRIDGE_SCHED] cs_sync_send failed")
                finally:
                    _last_sync_at = mono
            if _last_inbound_at is None or (mono - _last_inbound_at) >= _BRIDGE_INTERVAL_SEC:
                try:
                    print("### [CSBRIDGE_SCHED] running cs_inbound_poll")
                    call_command("cs_inbound_poll")
                except Exception:
                    logger.exception("[CSBRIDGE_SCHED] cs_inbound_poll failed")
                finally:
                    _last_inbound_at = mono

            # ===== 自動デプロイ: Gitee 監視(5分毎) =====
            global _last_deploy_check_at
            if _last_deploy_check_at is None or (mono - _last_deploy_check_at) >= _DEPLOY_INTERVAL_SEC:
                try:
                    _auto_deploy_check()
                except Exception:
                    logger.exception("[AUTO_DEPLOY] check failed")
                finally:
                    _last_deploy_check_at = mono

        except Exception:
            logger.exception("[CSTASKS_SCHED] error in scheduler loop")
        finally:
            close_old_connections()

        time.sleep(60)


def start_scheduler():
    """Django 起動時に一度だけバックグラウンドスレッドを起動する。"""
    global _scheduler_started
    if _scheduler_started:
        logger.info("[CSTASKS_SCHED] scheduler already started, skipping")
        return

    _scheduler_started = True
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    logger.info("[CSTASKS_SCHED] scheduler thread started")
