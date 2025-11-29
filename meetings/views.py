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


def _serialize_meeting(m: Meeting):
    return {
        "id": m.id,
        "visit_date": m.visit_date.strftime("%Y年%m月%d日") if m.visit_date else "",
        "visit_date_raw": m.visit_date.strftime("%Y-%m-%d") if m.visit_date else "",
        "visit_time": m.visit_time.strftime("%H:%M") if m.visit_time else "",
        "time_undecided_flag": m.time_undecided,
        "company_name": m.company_name,
        "last_name": m.last_name,
        "first_name": m.first_name,
        "title": m.title,
        "purpose": m.purpose,
        "location": m.location,
        "host_staff": m.host_staff,
        "cancelled_flag": m.cancelled,
    }

@login_required
def index(request):
    today = localdate()
    qs = Meeting.objects.filter(visit_date__gte=today).order_by("visit_date", "visit_time", "id")
    meetings = [_serialize_meeting(m) for m in qs]
    return render(request, "meetings/index.html", {"meetings": meetings})

@login_required
def add_meeting(request):
    MeetingFormSet = formset_factory(MeetingForm, extra=3)
    formset = MeetingFormSet(request.POST or None)
    time_choices = formset.empty_form.fields["visit_time"].widget.choices

    if request.method == "POST":
        has_error = False
        valid_forms = []

        for form in formset:
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
                    host_staff=data["host_staff"],
                    cancelled=False,
                )
            return redirect("meetings:index")

    return render(request, "meetings/add.html", {
        "formset": formset,
        "time_choices": time_choices,
    })


@login_required
@require_POST
def inline_update(request):
    """
    一覧画面のインライン編集用。
    id / field / value を JSON で受け取って Meeting を更新し、JSON を返す。
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
    Ajax と通常 POST の両方に対応（visitors と同じ思想）。
    """
    meeting = get_object_or_404(Meeting, pk=pk)

    # チェックボックスが送られてきたかどうかで判定
    checked = bool(request.POST.get("time_undecided"))
    meeting.time_undecided_flag = checked

    # 未定になったときは時間をクリアする仕様
    if checked:
        meeting.visit_time = None

    meeting.save()

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if is_ajax:
        return JsonResponse({
            "ok": True,
            "time_undecided": meeting.time_undecided_flag,
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
    """
    meeting = get_object_or_404(Meeting, pk=pk)

    checked = bool(request.POST.get("cancelled"))
    meeting.cancelled_flag = checked
    meeting.save()

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if is_ajax:
        return JsonResponse({
            "ok": True,
            "cancelled": meeting.cancelled_flag,
        })

    next_url = request.GET.get("next")
    if next_url == "index":
        return redirect("meetings:index")
    return redirect("meetings:index")

@login_required
def history(request):
    """過去分（本日より前）の訪問・WEB会議予定一覧"""
    today = timezone.localdate()

    qs = (
        Meeting.objects
        .filter(visit_date__lt=today)
        .order_by("-visit_date", "visit_time", "id")
    )

    meetings = []
    for m in qs:
        meetings.append({
            "id": m.id,
            # 表示用の日付（YYYY年MM月DD日）
            "visit_date": m.visit_date.strftime("%Y年%m月%d日") if m.visit_date else "",
            # カレンダーポップアップ用の生データ
            "visit_date_raw": m.visit_date.strftime("%Y-%m-%d") if m.visit_date else "",
            # 表示用時間
            "visit_time": m.visit_time.strftime("%H:%M") if m.visit_time else "",
            # フラグ類（テンプレート側と合わせる）
            "time_undecided_flag": bool(getattr(m, "time_undecided", False)),
            "cancelled_flag": bool(getattr(m, "cancelled", False)),
            # テキスト項目
            "company_name": m.company_name,
            "last_name": m.last_name,
            "first_name": m.first_name,
            "title": m.title,
            "purpose": m.purpose,
            "location": m.location,
            "host_staff": m.host_staff,
        })

    return render(request, "meetings/history.html", {"meetings": meetings})