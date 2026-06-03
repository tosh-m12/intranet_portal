# cs_tasks/views.py
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils import timezone

from .models import (
    Task,
    ProgressUpdate,
    SupervisorComment,
    WeeklyReportMailingList,
)
from .forms import TaskForm
from .permissions import (
    is_admin,
    can_edit_task,
    can_cancel_task,
    can_close_task,
    can_comment,
)

User = get_user_model()


def _display_name(user):
    """ユーザー表示名を「姓 名」で返す。空なら email。"""
    if user is None:
        return "（未割当）"
    last = (getattr(user, "last_name", "") or "").strip()
    first = (getattr(user, "first_name", "") or "").strip()
    if last or first:
        return (last + " " + first).strip()
    return user.get_username()


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


# =========================================================
# 入力言語の自動判定とフィールド振り分け
#   - ひらがな/カタカナを含めば JA、それ以外は ZH（簡易ヒューリスティック）
#   - 検出側のフィールドに保存し、逆側を空にする
#   - 逆側が空 → Mac 側翻訳ワークフローが「翻訳依頼」として検出する
# =========================================================
_KANA_RE = re.compile("[぀-ゟ゠-ヿ]")  # ひらがな + カタカナ


def _detect_lang(text):
    """ひらがな・カタカナを含めば 'ja'、それ以外は 'zh'。"""
    return "ja" if _KANA_RE.search(text or "") else "zh"


def _route_text(text):
    """入力テキストを (zh_field_value, ja_field_value) に振り分ける。

    検出言語側に text、逆側を空にする。Mac 側はこの「逆側 空」を見て
    翻訳が必要なエントリと判定する。
    """
    if _detect_lang(text) == "ja":
        return ("", text)
    return (text, "")


def _build_board(user, assignee_id=None):
    """
    担当者 > 顧客 > 課題 > 進捗 の3階層でグルーピングした
    課題ボード用のデータを組み立てる。
    戻り値: (groups, filtered_user)
      groups = [{
          "assignee", "assignee_id", "assignee_name", "total_rows",
          "clients": [{"client_name", "client_rows", "tasks": [task, ...]}],
      }]
      各 task には row_count / progress_list / can_edit を付与。
    """
    tasks_qs = (
        Task.objects.filter(is_cancelled=False)
        .select_related("owner", "assignee")
        .prefetch_related(
            "progress_updates__author",
            "progress_updates__comments__author",
        )
        .order_by("created_at", "id")
    )

    filtered_user = None
    if assignee_id:
        try:
            filtered_user = User.objects.get(pk=assignee_id)
            tasks_qs = tasks_qs.filter(assignee_id=filtered_user.id)
        except (User.DoesNotExist, ValueError):
            filtered_user = None

    admin = is_admin(user)

    # 担当者ID -> {顧客名 -> 課題リスト}
    groups_map = {}
    order = []
    for t in tasks_qs:
        t.progress_list = list(t.progress_updates.all())
        # 各進捗の行数を算出（コメント行 + 末尾の「＋コメントを追加」行）
        progress_rows_total = 0
        for p in t.progress_list:
            comment_list = list(p.comments.all())
            for c in comment_list:
                c.author_name = _display_name(c.author)
            # 表示行の組み立て：上長はコメント群＋追加行、一般はコメント（無ければ「—」1行）
            rows = [{"type": "comment", "obj": c} for c in comment_list]
            if admin:
                rows.append({"type": "add"})
            elif not rows:
                rows.append({"type": "none"})
            p.rows = rows
            p.comment_rows = len(rows)
            progress_rows_total += p.comment_rows
        # 行数 = タイトル行(1) + 進捗の全行 + 進捗追加行(1)
        t.row_count = 1 + progress_rows_total + 1
        t.can_edit = can_edit_task(user, t)

        akey = t.assignee_id  # None も可
        if akey not in groups_map:
            grp = {
                "assignee": t.assignee,
                "assignee_id": t.assignee_id,
                "assignee_name": _display_name(t.assignee),
                "clients_map": {},
                "clients_order": [],
                "total_rows": 0,
            }
            groups_map[akey] = grp
            order.append(akey)
        grp = groups_map[akey]

        ckey = t.client_name or ""
        if ckey not in grp["clients_map"]:
            grp["clients_map"][ckey] = {
                "client_name": t.client_name,   # 生の値（空もあり得る）
                "tasks": [],
                "client_rows": 0,
            }
            grp["clients_order"].append(ckey)
        client = grp["clients_map"][ckey]
        client["tasks"].append(t)
        client["client_rows"] += t.row_count
        grp["total_rows"] += t.row_count

    groups = []
    for akey in order:
        grp = groups_map[akey]
        clients = []
        for c in grp["clients_order"]:
            client = grp["clients_map"][c]
            client["task_ids"] = ",".join(str(t.id) for t in client["tasks"])
            # 顧客名のインライン編集は、グループ内の全課題を編集できる場合のみ許可
            client["can_edit"] = all(t.can_edit for t in client["tasks"])
            clients.append(client)
        grp["clients"] = clients
        del grp["clients_map"]
        del grp["clients_order"]
        groups.append(grp)

    # 担当者名で並べ替え（未割当は末尾）
    groups.sort(key=lambda g: (g["assignee_id"] is None, g["assignee_name"]))
    return groups, filtered_user


# =========================================================
# 課題・進捗ボード（メイン画面）。?assignee=<id> で担当者絞り込み
# =========================================================
@login_required
def index(request):
    assignee_id = request.GET.get("assignee")
    groups, filtered_user = _build_board(request.user, assignee_id)
    return render(request, "cs_tasks/board.html", {
        "groups": groups,
        "filtered_user": filtered_user,
        "filtered_user_name": _display_name(filtered_user) if filtered_user else "",
        "is_admin": is_admin(request.user),
        "board_title": "CS課題・進捗一覧",
    })


# =========================================================
# 自分の担当課題（ボードを自分で絞り込み）
# =========================================================
@login_required
def my_tasks(request):
    groups, _ = _build_board(request.user, assignee_id=request.user.id)
    return render(request, "cs_tasks/board.html", {
        "groups": groups,
        "filtered_user": request.user,
        "filtered_user_name": _display_name(request.user),
        "is_admin": is_admin(request.user),
        "board_title": "自分の担当課題",
        "is_my_view": True,
    })


# =========================================================
# 新規登録
# =========================================================
@login_required
def task_new(request):
    if request.method == "POST":
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.owner = request.user
            task.save()
            messages.success(request, "課題を登録しました。")
            return redirect("cs_tasks:index")
    else:
        # 担当者は初期値として現在ログイン中のユーザーを自動セット
        form = TaskForm(initial={"assignee": request.user})
    return render(request, "cs_tasks/new.html", {"form": form})


# =========================================================
# 課題のインライン追加（ボード上で完結・POST）
#   担当者は自動で現在ユーザー。課題名は28文字以内。
# =========================================================
@login_required
@require_POST
def task_add_inline(request):
    # フィールド名はブラウザのオートフィル誤作動回避のため非汎用名にしている
    title = (request.POST.get("cs_subj") or "").strip()[:28]
    client_name = (request.POST.get("cs_cust") or "").strip()
    progress = (request.POST.get("progress") or "").strip()

    if not title:
        messages.error(request, "課題名を入力してください。")
        return redirect("cs_tasks:index")

    title_zh, title_ja = _route_text(title)
    task = Task.objects.create(
        title=title_zh,
        title_ja=title_ja,
        client_name=client_name,
        owner=request.user,
        assignee=request.user,
    )
    if progress:
        p_zh, p_ja = _route_text(progress)
        ProgressUpdate.objects.create(
            task=task, author=request.user, content=p_zh, content_ja=p_ja
        )

    return redirect("cs_tasks:index")


# =========================================================
# 編集（課題の基本項目）
# =========================================================
@login_required
def task_edit(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    if not can_edit_task(request.user, task):
        return HttpResponseForbidden("この課題を編集する権限がありません。")

    if request.method == "POST":
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            form.save()
            messages.success(request, "課題を更新しました。")
            return redirect("cs_tasks:index")
    else:
        form = TaskForm(instance=task)
    return render(request, "cs_tasks/edit.html", {"form": form, "task": task})


# =========================================================
# 進捗追記（POST）
# =========================================================
@login_required
@require_POST
def add_progress(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    if not can_edit_task(request.user, task):
        return HttpResponseForbidden("この課題に進捗を追記する権限がありません。")

    content = (request.POST.get("content") or "").strip()
    if content:
        c_zh, c_ja = _route_text(content)
        ProgressUpdate.objects.create(
            task=task, author=request.user, content=c_zh, content_ja=c_ja
        )
        # 子の変更を往路差分スナップショットに載せるため親課題を touch
        task.save(update_fields=["updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 課題名（タイトル）のその場編集（POST）。28文字以内。
# =========================================================
@login_required
@require_POST
def edit_title(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    if not can_edit_task(request.user, task):
        return HttpResponseForbidden("この課題を編集する権限がありません。")

    title = (request.POST.get("cs_subj") or "").strip()[:28]
    if title:
        t_zh, t_ja = _route_text(title)
        task.title = t_zh
        task.title_ja = t_ja
        task.save(update_fields=["title", "title_ja", "updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 顧客名のその場編集（POST）。結合セルのため、対象グループの
# 全課題（task_ids）の client_name を更新する。
# =========================================================
@login_required
@require_POST
def edit_client(request):
    raw_ids = (request.POST.get("task_ids") or "").split(",")
    ids = [i for i in raw_ids if i.strip().isdigit()]
    client_name = (request.POST.get("cs_cust") or "").strip()

    for task in Task.objects.filter(pk__in=ids):
        if can_edit_task(request.user, task):
            task.client_name = client_name
            task.save(update_fields=["client_name", "updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 進捗の編集（その場編集・POST）。日付(created_at)は変更しない。
# =========================================================
@login_required
@require_POST
def edit_progress(request, progress_id):
    progress = get_object_or_404(ProgressUpdate, pk=progress_id)
    if not can_edit_task(request.user, progress.task):
        return HttpResponseForbidden("この進捗を編集する権限がありません。")

    content = (request.POST.get("content") or "").strip()
    if content:
        # content のみ更新（created_at は auto_now_add のため不変）
        c_zh, c_ja = _route_text(content)
        progress.content = c_zh
        progress.content_ja = c_ja
        progress.save(update_fields=["content", "content_ja"])
        # 子の編集を往路差分スナップショットに載せるため親課題を touch
        progress.task.save(update_fields=["updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 上長コメント追記（進捗1件に対して複数可・POST, is_staff のみ）
# =========================================================
@login_required
@require_POST
def add_comment(request, progress_id):
    progress = get_object_or_404(ProgressUpdate, pk=progress_id)
    if not can_comment(request.user):
        return HttpResponseForbidden("コメントを付与する権限がありません。")

    content = (request.POST.get("content") or "").strip()
    if content:
        c_zh, c_ja = _route_text(content)
        SupervisorComment.objects.create(
            progress=progress,
            author=request.user,
            content=c_zh,
            content_ja=c_ja,
        )
        # 子の変更を往路差分スナップショットに載せるため親課題を touch
        progress.task.save(update_fields=["updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 上長コメントの編集（その場編集・POST, is_staff のみ）。投稿者は変更しない。
# =========================================================
@login_required
@require_POST
def edit_comment(request, comment_id):
    comment = get_object_or_404(SupervisorComment, pk=comment_id)
    if not can_comment(request.user):
        return HttpResponseForbidden("コメントを編集する権限がありません。")

    content = (request.POST.get("content") or "").strip()
    if content:
        c_zh, c_ja = _route_text(content)
        comment.content = c_zh
        comment.content_ja = c_ja
        comment.save(update_fields=["content", "content_ja"])
        # 子の編集を往路差分スナップショットに載せるため親課題を touch
        comment.progress.task.save(update_fields=["updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 課題全体の完了 ⇔ 再開トグル（POST, is_staff のみ）
# =========================================================
@login_required
@require_POST
def toggle_complete(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    if not can_close_task(request.user):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "クローズ操作の権限がありません。"}, status=403
            )
        return HttpResponseForbidden("クローズ操作の権限がありません。")

    if task.is_closed:
        # 再開：課題と配下の進捗を連動して全て再開
        task.is_closed = False
        task.completed_at = None
        task.completed_by = None
        task.progress_updates.update(is_closed=False, closed_at=None, closed_by=None)
    else:
        # クローズ：課題と配下の進捗を連動して全てクローズ
        now = timezone.now()
        task.is_closed = True
        task.completed_at = now
        task.completed_by = request.user
        task.progress_updates.update(
            is_closed=True, closed_at=now, closed_by=request.user
        )
    task.save(update_fields=["is_closed", "completed_at", "completed_by", "updated_at"])

    if _is_ajax(request):
        return JsonResponse({"ok": True, "is_closed": task.is_closed})
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 進捗行のクローズ ⇔ 再開トグル（POST, is_staff のみ）
# =========================================================
@login_required
@require_POST
def toggle_progress_close(request, progress_id):
    progress = get_object_or_404(ProgressUpdate, pk=progress_id)
    if not can_close_task(request.user):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "クローズ操作の権限がありません。"}, status=403
            )
        return HttpResponseForbidden("クローズ操作の権限がありません。")

    if progress.is_closed:
        progress.is_closed = False
        progress.closed_at = None
        progress.closed_by = None
    else:
        progress.is_closed = True
        progress.closed_at = timezone.now()
        progress.closed_by = request.user
    progress.save(update_fields=["is_closed", "closed_at", "closed_by"])

    if _is_ajax(request):
        return JsonResponse({"ok": True, "is_closed": progress.is_closed})
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 中止 ⇔ 復活トグル（POST, 論理削除。上長 or 登録者）
# =========================================================
@login_required
@require_POST
def toggle_cancel(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    if not can_cancel_task(request.user, task):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "中止操作の権限がありません。"}, status=403
            )
        return HttpResponseForbidden("中止操作の権限がありません。")

    if task.is_cancelled:
        task.is_cancelled = False
        task.cancelled_at = None
    else:
        task.is_cancelled = True
        task.cancelled_at = timezone.now()
    task.save(update_fields=["is_cancelled", "cancelled_at", "updated_at"])

    if _is_ajax(request):
        return JsonResponse({"ok": True, "is_cancelled": task.is_cancelled})
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# メーリングリスト管理（is_staff のみ）
# =========================================================
@user_passes_test(is_admin)
@login_required
def mailing_list(request):
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add":
            email = request.POST.get("email", "").strip().lower()
            name = request.POST.get("name", "").strip()
            if email:
                WeeklyReportMailingList.objects.get_or_create(
                    email=email, defaults={"name": name, "is_active": True}
                )
                messages.success(request, "宛先を追加しました。")
            else:
                messages.error(request, "メールアドレスを入力してください。")

        elif action == "delete":
            entry_id = request.POST.get("entry_id")
            WeeklyReportMailingList.objects.filter(pk=entry_id).delete()
            messages.success(request, "宛先を削除しました。")

        elif action == "toggle":
            entry_id = request.POST.get("entry_id")
            entry = WeeklyReportMailingList.objects.filter(pk=entry_id).first()
            if entry:
                entry.is_active = not entry.is_active
                entry.save(update_fields=["is_active"])

        return redirect("cs_tasks:mailing_list")

    entries = WeeklyReportMailingList.objects.all()
    return render(request, "cs_tasks/mailing_list.html", {"entries": entries})


# =========================================================
# 週報プレビュー・手動送信（is_staff のみ）
# =========================================================
@user_passes_test(is_admin)
@login_required
def weekly_report(request):
    from .email_utils import build_weekly_report_context, send_weekly_report
    from django.utils.timezone import localdate

    if request.method == "POST" and request.POST.get("action") == "send":
        result = send_weekly_report(ignore_schedule=True)
        if result.get("sent"):
            recipients = result.get("recipients") or []
            messages.success(
                request,
                f"週報を送信しました。宛先: {', '.join(recipients)}",
            )
        else:
            messages.error(
                request, f"週報の送信に失敗しました。{result.get('reason', '')}"
            )
        return redirect("cs_tasks:weekly_report")

    today = localdate()
    report = build_weekly_report_context(today)
    return render(request, "cs_tasks/weekly_report.html", {"report": report})
