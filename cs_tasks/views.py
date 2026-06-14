# cs_tasks/views.py
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

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
        return _("（未割当）")
    last = (getattr(user, "last_name", "") or "").strip()
    first = (getattr(user, "first_name", "") or "").strip()
    if last or first:
        return (last + " " + first).strip()
    return user.get_username()


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


# =========================================================
# 入力言語の自動判定とフィールド振り分け
#   - 検出側のフィールドに保存し、逆側を空にする
#   - 逆側が空 → Mac 側翻訳ワークフローが「翻訳依頼」として検出する
#
# 判定方針（社内側の入力は日本語が大半である点を前提にした順序）:
#   1. ひらがな・カタカナを含む → ja（確実な日本語シグナル）
#   2. 簡体字専用の字を含む      → zh（日本語の漢字には現れない字形）
#   3. それ以外（共通漢字のみ / 英数のみ / 空）→ ja に倒す
# 旧実装は「かな無し＝zh」だったため、漢字のみの日本語（例: 確認中・対応完了）が
# 中文と誤判定され、Mac 側で誤って再翻訳される不具合(C-4)があった。これを是正する。
# =========================================================
_KANA_RE = re.compile("[぀-ゟ゠-ヿ]")  # ひらがな + カタカナ

# 簡体字専用の常用字（日本語の新字体・旧字体のいずれにも現れない字形）。
# 1 文字でも含めば中文と判定する。共通漢字（国・本・文 等）は意図的に含めない。
_ZH_ONLY_CHARS = set(
    "这那个们你说对见时经长问车东马应该没贵卖图关进还让边过习试觉谢请给"
    "难题样现单总产业为买书乐价众优传伟实击义乌乔乡丰临丽举动务华协卫"
    "厂厅历压县发变厌电话维护质"
)


def _detect_lang(text):
    """入力テキストの言語を 'ja' / 'zh' で返す（詳細は上のコメント参照）。"""
    t = text or ""
    if _KANA_RE.search(t):
        return "ja"
    if any(ch in _ZH_ONLY_CHARS for ch in t):
        return "zh"
    return "ja"


def _route_text(text):
    """入力テキストを (zh_field_value, ja_field_value) に振り分ける。

    検出言語側に text、逆側を空にする。Mac 側はこの「逆側 空」を見て
    翻訳が必要なエントリと判定する。
    """
    if _detect_lang(text) == "ja":
        return ("", text)
    return (text, "")


def _build_board(user, assignee_id=None, category=None, for_report=False):
    """
    担当者 > 顧客 > 課題 > 進捗 の3階層でグルーピングした
    課題ボード用のデータを組み立てる。
    戻り値: (groups, filtered_user)
      groups = [{
          "assignee", "assignee_id", "assignee_name", "total_rows",
          "clients": [{"client_name", "client_rows", "tasks": [task, ...]}],
      }]
      各 task には row_count / progress_list / can_edit を付与。

    for_report=True … レポート(読み取り専用)用。進捗1件=1行、コメント列・追加行なし。
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

    if category:
        tasks_qs = tasks_qs.filter(category=category)

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
            if for_report:
                # レポートはコメント列なし・読み取り専用。進捗1件=1行。
                progress_rows_total += 1
                continue
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
        # 行数 = タイトル行(1) + 進捗の全行 [+ 進捗追加行(1)]。レポートは追加行なし。
        t.row_count = 1 + progress_rows_total + (0 if for_report else 1)
        t.can_edit = False if for_report else can_edit_task(user, t)

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
_CATEGORY_TITLE = {
    Task.CATEGORY_EXISTING: "既存顧客課題",
    Task.CATEGORY_NEW: "新規顧客課題",
    Task.CATEGORY_INTERNAL: "部内課題",
    Task.CATEGORY_INCIDENT: "クレーム・インシデント (Bad News First)",
}


@login_required
def index(request):
    assignee_id = None   # 担当者名クリックでの絞り込みは廃止
    cat = request.GET.get("cat") or Task.CATEGORY_EXISTING
    if cat not in _CATEGORY_TITLE:
        cat = Task.CATEGORY_EXISTING
    groups, filtered_user = _build_board(request.user, assignee_id, category=cat)
    return render(request, "cs_tasks/board.html", {
        "groups": groups,
        "filtered_user": filtered_user,
        "filtered_user_name": _display_name(filtered_user) if filtered_user else "",
        "is_admin": is_admin(request.user),
        "board_title": _CATEGORY_TITLE[cat],
        "active_tab": cat,
        "current_category": cat,            # 新規追加時に引き継ぐ区分
        "hide_client": cat == Task.CATEGORY_INTERNAL,
    })


# =========================================================
# レポート（区分別の課題ボードを縦に並べた読み取り専用ビュー）
# 上から クレーム・インシデント → 既存 → 新規 → 部内。
# =========================================================
_REPORT_CATEGORY_ORDER = [
    Task.CATEGORY_INCIDENT,
    Task.CATEGORY_EXISTING,
    Task.CATEGORY_NEW,
    Task.CATEGORY_INTERNAL,
]


@login_required
def report(request):
    sections = []
    for cat in _REPORT_CATEGORY_ORDER:
        groups, _ = _build_board(request.user, category=cat, for_report=True)
        sections.append({
            "key": cat,
            "label": _CATEGORY_TITLE[cat],
            "hide_client": cat == Task.CATEGORY_INTERNAL,
            "groups": groups,
        })
    return render(request, "cs_tasks/report.html", {
        "sections": sections,
        "active_tab": "report",
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
            messages.success(request, _("課題を登録しました。"))
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
    category = request.POST.get("category") or Task.CATEGORY_EXISTING
    if category not in _CATEGORY_TITLE:
        category = Task.CATEGORY_EXISTING

    if not title:
        messages.error(request, _("課題名を入力してください。"))
        return redirect(f"{reverse('cs_tasks:index')}?cat={category}")

    # 部内課題は顧客名を持たない
    if category == Task.CATEGORY_INTERNAL:
        client_name = ""

    title_zh, title_ja = _route_text(title)
    task = Task.objects.create(
        category=category,
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

    return redirect(f"{reverse('cs_tasks:index')}?cat={category}")


# =========================================================
# 編集（課題の基本項目）
# =========================================================
@login_required
def task_edit(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    if not can_edit_task(request.user, task):
        return HttpResponseForbidden(_("この課題を編集する権限がありません。"))

    if request.method == "POST":
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            form.save()
            messages.success(request, _("課題を更新しました。"))
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
        return HttpResponseForbidden(_("この課題に進捗を追記する権限がありません。"))

    content = (request.POST.get("content") or "").strip()
    if content:
        c_zh, c_ja = _route_text(content)
        exec_date = parse_date(request.POST.get("execution_date") or "") or timezone.localdate()
        ProgressUpdate.objects.create(
            task=task, author=request.user, content=c_zh, content_ja=c_ja,
            execution_date=exec_date,
        )
        # 子の変更を往路差分スナップショットに載せるため親課題を touch
        task.save(update_fields=["updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 進捗の実施日を編集（カレンダー選択。記入忘れで後日入れた時に実際の日付を入れる）
# =========================================================
@login_required
@require_POST
def edit_progress_date(request, progress_id):
    progress = get_object_or_404(ProgressUpdate, pk=progress_id)
    if not can_edit_task(request.user, progress.task):
        return HttpResponseForbidden(_("この進捗を編集する権限がありません。"))

    d = parse_date(request.POST.get("execution_date") or "")
    if d:
        progress.execution_date = d
        progress.save(update_fields=["execution_date"])
        progress.task.save(update_fields=["updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 課題の詳細（内容）のその場編集（POST）。タイトル直下に表示。
# =========================================================
@login_required
@require_POST
def edit_description(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    if not can_edit_task(request.user, task):
        return HttpResponseForbidden(_("この課題を編集する権限がありません。"))

    desc = (request.POST.get("description") or "").strip()
    d_zh, d_ja = _route_text(desc) if desc else ("", "")
    task.description = d_zh
    task.description_ja = d_ja
    task.save(update_fields=["description", "description_ja", "updated_at"])
    return redirect(request.POST.get("next") or "cs_tasks:index")


# =========================================================
# 課題名（タイトル）のその場編集（POST）。28文字以内。
# =========================================================
@login_required
@require_POST
def edit_title(request, task_id):
    task = get_object_or_404(Task, pk=task_id)
    if not can_edit_task(request.user, task):
        return HttpResponseForbidden(_("この課題を編集する権限がありません。"))

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
        return HttpResponseForbidden(_("コメントを付与する権限がありません。"))

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
        return HttpResponseForbidden(_("コメントを編集する権限がありません。"))

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
                {"ok": False, "error": _("クローズ操作の権限がありません。")}, status=403
            )
        return HttpResponseForbidden(_("クローズ操作の権限がありません。"))

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
                {"ok": False, "error": _("クローズ操作の権限がありません。")}, status=403
            )
        return HttpResponseForbidden(_("クローズ操作の権限がありません。"))

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
                {"ok": False, "error": _("中止操作の権限がありません。")}, status=403
            )
        return HttpResponseForbidden(_("中止操作の権限がありません。"))

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
                messages.success(request, _("宛先を追加しました。"))
            else:
                messages.error(request, _("メールアドレスを入力してください。"))

        elif action == "delete":
            entry_id = request.POST.get("entry_id")
            WeeklyReportMailingList.objects.filter(pk=entry_id).delete()
            messages.success(request, _("宛先を削除しました。"))

        elif action == "toggle":
            entry_id = request.POST.get("entry_id")
            entry = WeeklyReportMailingList.objects.filter(pk=entry_id).first()
            if entry:
                entry.is_active = not entry.is_active
                entry.save(update_fields=["is_active"])

        return redirect("cs_tasks:mailing_list")

    entries = WeeklyReportMailingList.objects.all()
    return render(request, "cs_tasks/mailing_list.html", {"entries": entries})
