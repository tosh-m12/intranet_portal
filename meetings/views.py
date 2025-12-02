# meetings/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.forms import formset_factory
from django.contrib.auth.decorators import login_required
from django.utils.timezone import localdate
from django.utils import timezone
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST

from .models import Meeting
from .forms import MeetingForm
import json
import datetime


def _get_display_name(user):
    """
    visitors アプリと同仕様：
    - ユーザーの「姓 名」があればそれを優先
    - なければ Django 的な get_full_name()
    - それもなければ email or username
    """
    last = (user.last_name or "").strip()
    first = (user.first_name or "").strip()
    full = f"{last} {first}".strip()
    if full:
        return full

    if user.get_full_name():
        return user.get_full_name()

    if user.email:
        return user.email

    return user.get_username()


def _can_edit_meeting(meeting: Meeting, user):
    """
    編集権限：
    - スーパーユーザー or staff は全行OK
    - それ以外は created_by が自分の行のみ
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    creator_id = getattr(meeting, "created_by_id", None)
    return creator_id == user.id


def _serialize_meeting(m: Meeting, user):
    can_edit = _can_edit_meeting(m, user)
    return {
        "id": m.id,
        "visit_date": m.visit_date.strftime("%Y年%m月%d日") if m.visit_date else "",
        "visit_date_raw": m.visit_date.strftime("%Y-%m-%d") if m.visit_date else "",
        "visit_time": m.visit_time.strftime("%H:%M") if m.visit_time else "",
        "time_undecided_flag": bool(getattr(m, "time_undecided", False)),
        "company_name": m.company_name,
        "last_name": m.last_name,
        "first_name": m.first_name,
        "title": m.title,
        "purpose": m.purpose,
        "location": m.location,
        "host_staff": m.host_staff,
        "cancelled_flag": bool(getattr(m, "cancelled", False)),
        # 権限フラグ（visitors と同思想）
        "can_edit": can_edit,
        "can_toggle_undecided": can_edit,
        "can_toggle_cancel": can_edit,
    }


@login_required
def index(request):
    today = localdate()
    qs = (
        Meeting.objects
        .filter(visit_date__gte=today)
        .order_by("visit_date", "visit_time", "id")
    )
    meetings = [_serialize_meeting(m, request.user) for m in qs]
    return render(request, "meetings/index.html", {"meetings": meetings})


@login_required
def add_meeting(request):
    """
    visitors.add_visitor と同様の仕様：
    - 行は formset で複数入力
    - 全項目空行はスキップ
    - host_staff は画面入力値ではなく「ログインユーザー名」を強制採用
    - created_by に request.user をセット
    """
    MeetingFormSet = formset_factory(MeetingForm, extra=3)
    formset = MeetingFormSet(request.POST or None)
    time_choices = formset.empty_form.fields["visit_time"].widget.choices

    host_name = _get_display_name(request.user)

    if request.method == "POST":
        has_error = False
        valid_forms = []

        for form in formset:
            # 全項目空欄ならスキップ
            all_empty = all(
                not form.data.get(f"{form.prefix}-{field}")
                for field in form.fields
            )
            if all_empty:
                continue

            if form.is_valid():
                valid_forms.append(form)
            else:
                has_error = True

        if not has_error and valid_forms:
            for form in valid_forms:
                data = form.cleaned_data
                Meeting.objects.create(
                    visit_date=data["visit_date"],
                    visit_time=data["visit_time"] if not data.get("time_undecided") else None,
                    time_undecided=data.get("time_undecided", False),
                    company_name=data["company_name"],
                    last_name=data["last_name"],
                    first_name=data["first_name"],
                    title=data.get("title", ""),
                    purpose=data.get("purpose", ""),
                    location=data["location"],
                    host_staff=_get_display_name(request.user),  # 表示用：フルネーム
                    cancelled=False,
                    created_by=request.user,                    # 権限判定用
                )

            return redirect("meetings:index")

    return render(request, "meetings/add.html", {
        "formset": formset,
        "time_choices": time_choices,
        "host_name": host_name,
    })


@login_required
@require_POST
def inline_update(request):
    """
    一覧画面のインライン編集用。
    id / field / value を JSON で受け取って Meeting を更新し、JSON を返す。
    visitors.inline_update と同じ思想で権限チェックを追加。
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    pk = data.get("id")
    field = data.get("field")
    value = data.get("value", "")

    if not pk or not field:
        return HttpResponseBadRequest("Missing parameters")

    meeting = get_object_or_404(Meeting, pk=pk)

    # 権限チェック
    if not _can_edit_meeting(meeting, request.user):
        return JsonResponse(
            {"ok": False, "error": "この行を編集する権限がありません。"},
            status=403,
        )

    try:
        if field == "visit_date":
            # "YYYY-MM-DD" 形式で送られてくる想定
            if value:
                meeting.visit_date = datetime.datetime.strptime(value, "%Y-%m-%d").date()
            else:
                meeting.visit_date = None

        elif field == "visit_time":
            if value:
                meeting.visit_time = datetime.datetime.strptime(value, "%H:%M").time()
            else:
                meeting.visit_time = None

        else:
            # その他のテキスト項目（company_name 等）
            if hasattr(meeting, field):
                setattr(meeting, field, value)
            else:
                return HttpResponseBadRequest("Unknown field")

        meeting.save()

        # 返却値は visitors と同じ形にしておく
        resp_value = value
        if field == "visit_date" and meeting.visit_date:
            resp_value = meeting.visit_date.strftime("%Y-%m-%d")
        elif field == "visit_time" and meeting.visit_time:
            resp_value = meeting.visit_time.strftime("%H:%M")

        return JsonResponse({
            "ok": True,
            "value": resp_value,
        })

    except Exception as e:
        return JsonResponse({
            "ok": False,
            "error": str(e),
        })


@login_required
@require_POST
def toggle_undecided(request, pk):
    """
    未定チェックボックスのトグル。
    visitors.toggle_undecided と同様に権限チェック・JSON 返却。
    """
    meeting = get_object_or_404(Meeting, pk=pk)

    if not _can_edit_meeting(meeting, request.user):
        return JsonResponse(
            {"ok": False, "error": "この行を更新する権限がありません。"},
            status=403,
        )

    # チェックボックスが送られてきたかどうかで判定
    checked = bool(request.POST.get("time_undecided"))
    meeting.time_undecided = checked  # ★ 正しいフィールド名に修正

    # 未定になったときは時間をクリアする仕様
    if checked:
        meeting.visit_time = None

    meeting.save()

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if is_ajax:
        return JsonResponse({
            "ok": True,
            "time_undecided": meeting.time_undecided,
            "visit_time": meeting.visit_time.strftime("%H:%M") if meeting.visit_time else "",
        })

    next_url = request.GET.get("next")
    if next_url == "index":
        return redirect("meetings:index")
    return redirect("meetings:index")


@login_required
@require_POST
def cancel_meeting(request, pk):
    """
    取消スイッチのトグル。
    visitors.cancel_visitor と同様に権限チェック。
    """
    meeting = get_object_or_404(Meeting, pk=pk)

    if not _can_edit_meeting(meeting, request.user):
        return JsonResponse(
            {"ok": False, "error": "この行を更新する権限がありません。"},
            status=403,
        )

    checked = bool(request.POST.get("cancelled"))
    meeting.cancelled = checked  # ★ 正しいフィールド名に修正
    meeting.save()

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if is_ajax:
        return JsonResponse({
            "ok": True,
            "cancelled": meeting.cancelled,
        })

    next_url = request.GET.get("next")
    if next_url == "index":
        return redirect("meetings:index")
    return redirect("meetings:index")


@login_required
def history(request):
    """
    過去分（本日を含む）訪問・WEB会議予定一覧。
    visitors.history と同じく can_edit 付きで返す。
    """
    today = localdate()  # すでに import 済み

    qs = (
        Meeting.objects
        .filter(visit_date__lte=today)      # 「本日を含む過去」にするなら <=
        .order_by("-visit_date", "visit_time", "id")
    )

    meetings = [_serialize_meeting(m, request.user) for m in qs]

    return render(request, "meetings/history.html", {"meetings": meetings})
