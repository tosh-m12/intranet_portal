# meetings/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.forms import formset_factory
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils.timezone import localdate
from django.utils import timezone
from django.http import (
    JsonResponse,
    HttpResponseBadRequest,
    HttpResponse,
)
from django.views.decorators.http import require_POST
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from .models import (
    Meeting,
    MeetingMailRecipient,
    MeetingMailConfig,
)
from .forms import MeetingForm

# ★ メール本文生成・送信ユーティリティ（別ファイルで実装予定）
from .email_utils import (
    get_meetings_for_mail,
    send_daily_email,
)

import json
import datetime
import csv
import io
import logging

logger = logging.getLogger(__name__)


# =========================================================
# 共通ユーティリティ
# =========================================================

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


def _is_admin(user):
    return user.is_superuser or user.is_staff


# =========================================================
# 一覧・新規登録
# =========================================================

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


def _contact_candidates():
    """訪問(Meeting)+来客(Visitor)のキャンセル除外データから入力候補を作る。

    クリーニング済みの相手先表記を候補に出し、表記ゆれ再発を入口で防ぐ。
    会社→姓→名 が一意に決まれば JS が名・役職を補完する。visitors 側と対。
    """
    from visitors.models import Visitor

    seen, contacts = set(), []
    companies, titles = set(), set()
    for Model in (Meeting, Visitor):
        rows = Model.objects.filter(cancelled=False).values(
            "company_name", "last_name", "first_name", "title")
        for r in rows:
            co = (r["company_name"] or "").strip()
            ln = (r["last_name"] or "").strip()
            fn = (r["first_name"] or "").strip()
            ti = (r["title"] or "").strip()
            if co:
                companies.add(co)
            if ti:
                titles.add(ti)
            if co or ln or fn:
                key = (co, ln, fn, ti)
                if key not in seen:
                    seen.add(key)
                    contacts.append({"c": co, "l": ln, "f": fn, "t": ti})
    return {
        "cand_companies": sorted(companies),
        "cand_titles": sorted(titles),
        "cand_lastnames": sorted({c["l"] for c in contacts if c["l"]}),
        "cand_firstnames": sorted({c["f"] for c in contacts if c["f"]}),
        "cand_contacts_json": json.dumps(contacts, ensure_ascii=False),
    }


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
        **_contact_candidates(),
    })


# =========================================================
# インライン更新系
# =========================================================

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
            {"ok": False, "error": _("この行を編集する権限がありません。")},
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
            {"ok": False, "error": _("この行を更新する権限がありません。")},
            status=403,
        )

    # チェックボックスが送られてきたかどうかで判定
    checked = bool(request.POST.get("time_undecided"))
    meeting.time_undecided = checked

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
            {"ok": False, "error": _("この行を更新する権限がありません。")},
            status=403,
        )

    checked = bool(request.POST.get("cancelled"))
    meeting.cancelled = checked
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


# =========================================================
# 過去一覧
# =========================================================

@login_required
def history(request):
    """
    過去分（本日を含む）訪問・WEB会議予定一覧。
    visitors.history と同じく can_edit 付きで返す。
    """
    today = localdate()

    qs = (
        Meeting.objects
        .filter(visit_date__lte=today)      # 「本日を含む過去」
        .order_by("-visit_date", "visit_time", "id")
    )

    meetings = [_serialize_meeting(m, request.user) for m in qs]

    return render(request, "meetings/history.html", {"meetings": meetings})


# =========================================================
# 各種設定（送信時刻・メーリングリスト・CSVメンテ）
# =========================================================

@login_required
@user_passes_test(_is_admin)
def settings_view(request):
    """
    訪問・WEB会議メールの設定画面。
    - 送信時刻（毎日）
    - メーリングリスト
    - Meeting 一覧の CSV DL/UL
    """
    # 1レコードだけを使う想定
    config, _ = MeetingMailConfig.objects.get_or_create(pk=1)

    if request.method == "POST":
        # ▼ 送信時刻
        send_hour_str = request.POST.get("send_hour", "09")
        send_minute_str = request.POST.get("send_minute", "00")
        try:
            send_hour = int(send_hour_str)
            send_minute = int(send_minute_str)
            config.send_time = datetime.time(send_hour, send_minute)
        except ValueError:
            config.send_time = datetime.time(9, 0)

        # 自動送信方式は Django 内部スケジューラ固定とする
        config.mode = MeetingMailConfig.MODE_DJANGO

        # ▼ メーリングリスト再保存
        MeetingMailRecipient.objects.all().delete()
        emails = request.POST.getlist("emails")
        for e in emails:
            e = (e or "").strip()
            if e:
                MeetingMailRecipient.objects.create(email=e)

        config.save()
        messages.success(request, _("設定を保存しました。"))
        return redirect("meetings:settings")

    # ===== GET（画面表示） =====
    mailing_list = list(MeetingMailRecipient.objects.values_list("email", flat=True))

    send_hour = config.send_time.hour if config.send_time else 9
    send_minute = config.send_time.minute if config.send_time else 0

    hours = [f"{h:02d}" for h in range(0, 24)]
    minutes = [f"{m:02d}" for m in (0, 15, 30, 45)]

    context = {
        "mailing_list": mailing_list,
        "send_hour": f"{send_hour:02d}",
        "send_minute": f"{send_minute:02d}",
        "hours": hours,
        "minutes": minutes,
        "config": config,
    }
    return render(request, "meetings/settings.html", context)


# =========================================================
# 設定画面：Meeting 一覧 CSV ダウンロード
# =========================================================

@login_required
@user_passes_test(_is_admin)
def download_settings_csv(request, target):
    """
    Meeting 一覧の CSV ダウンロード。
    target == "meeting_list" のみ対応。
    """
    if target != "meeting_list":
        return HttpResponseBadRequest("Unknown CSV target")

    response = HttpResponse(
        content_type="text/csv; charset=utf-8"
    )
    filename = f"meeting_list_{timezone.now().strftime('%Y%m%d')}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    writer.writerow([
        "id",
        "visit_date",
        "visit_time",
        "time_undecided",
        "company_name",
        "last_name",
        "first_name",
        "title",
        "purpose",
        "location",
        "host_staff",
        "cancelled",
    ])

    qs = Meeting.objects.all().order_by("visit_date", "visit_time", "id")
    for m in qs:
        visit_date = m.visit_date.strftime("%Y-%m-%d") if m.visit_date else ""
        visit_time = m.visit_time.strftime("%H:%M") if m.visit_time else ""
        time_undecided = "1" if m.time_undecided else "0"
        cancelled = "1" if m.cancelled else "0"

        writer.writerow([
            m.id,
            visit_date,
            visit_time,
            time_undecided,
            m.company_name,
            m.last_name,
            m.first_name,
            m.title,
            m.purpose,
            m.location,
            m.host_staff,
            cancelled,
        ])

    return response


# =========================================================
# 設定画面：Meeting 一覧 CSV アップロード
# =========================================================

@login_required
@user_passes_test(_is_admin)
def upload_meeting_csv(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    csv_file = request.FILES.get("csv_file")
    if not csv_file:
        messages.error(request, _("CSVファイルが選択されていません。"))
        return redirect("meetings:settings")

    try:
        text_file = io.TextIOWrapper(csv_file.file, encoding="utf-8-sig")
        reader = csv.DictReader(text_file)

        new_meetings = []

        def to_bool(val):
            s = (val or "").strip().lower()
            return s in ("1", "true", "t", "yes", "y", "on")

        for row in reader:
            visit_date_str = (row.get("visit_date") or "").strip()
            visit_time_str = (row.get("visit_time") or "").strip()
            time_undecided_str = (row.get("time_undecided") or "").strip()
            cancelled_str = (row.get("cancelled") or "").strip()

            visit_date = None
            if visit_date_str:
                visit_date = datetime.datetime.strptime(visit_date_str, "%Y-%m-%d").date()

            visit_time_str = visit_time_str.strip()
            if not visit_time_str:
                visit_time = None
            else:
                try:
                    visit_time = datetime.datetime.strptime(visit_time_str, "%H:%M").time()
                except ValueError:
                    # HH:MM:SS の場合にも対応
                    visit_time = datetime.datetime.strptime(visit_time_str, "%H:%M:%S").time()

            time_undecided = to_bool(time_undecided_str)
            cancelled = to_bool(cancelled_str)

            if time_undecided:
                visit_time = None

            m = Meeting(
                visit_date=visit_date,
                visit_time=visit_time,
                time_undecided=time_undecided,
                company_name=(row.get("company_name") or "").strip(),
                last_name=(row.get("last_name") or "").strip(),
                first_name=(row.get("first_name") or "").strip(),
                title=(row.get("title") or "").strip(),
                purpose=(row.get("purpose") or "").strip(),
                location=(row.get("location") or "").strip(),
                host_staff=(row.get("host_staff") or "").strip(),
                cancelled=cancelled,
            )
            new_meetings.append(m)

        with transaction.atomic():
            Meeting.objects.all().delete()
            Meeting.objects.bulk_create(new_meetings)

        messages.success(
            request,
            _("Meeting データをCSVから再登録しました（%(count)s件）。")
            % {"count": len(new_meetings)},
        )

    except Exception as e:
        logger.exception("upload_meeting_csv error")
        messages.error(
            request,
            _("CSVの読み込み中にエラーが発生しました: %(error)s") % {"error": e},
        )

    return redirect("meetings:settings")


# =========================================================
# メールプレビュー・今すぐ送信
# =========================================================

@login_required
@user_passes_test(_is_admin)
def preview_email(request):
    """
    設定画面からのメールプレビュー表示。
    - 今日以降の Meeting を取得
    - meetings/email_template.html で HTML 本文生成
    - meetings/email_preview.html の中に埋め込んで表示
    """
    today = localdate()
    meetings = get_meetings_for_mail(today)

    html_body = render_to_string(
        "meetings/email_template.html",
        {
            "meetings": meetings,
            "today": today,
        },
    )

    return render(
        request,
        "meetings/email_preview.html",
        {
            "email_html": mark_safe(html_body),
        },
    )


@login_required
@user_passes_test(_is_admin)
def run_email(request):
    """
    「今すぐ送信」ボタン用。
    - ignore_holiday=True で強制送信
    - 成功時は last_sent_date を更新
    """
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    try:
        result = send_daily_email(ignore_holiday=True)

        today = timezone.localdate()
        config, _ = MeetingMailConfig.objects.get_or_create(pk=1)

        sent = False
        detail = ""

        if isinstance(result, dict):
            sent = result.get("sent", False)
            detail = result.get("reason", "")
        else:
            sent = (result == "ok")
            if not sent:
                detail = str(result)

        if sent:
            config.last_sent_date = today
            config.save(update_fields=["last_sent_date"])

            recipients_str = ""
            if isinstance(result, dict):
                recipients = result.get("recipients") or []
                if recipients:
                    recipients_str = _(" 宛先: %(recipients)s") % {
                        "recipients": ", ".join(recipients)
                    }

            messages.success(
                request,
                _("📨 メールを送信しました。%(recipients)s")
                % {"recipients": recipients_str},
            )
            logger.info(f"[MEETING_MAIL_VIEW] manual send ok, last_sent_date={today}, result={result}")
        else:
            messages.error(
                request,
                _("⚠ メール送信に失敗しました。%(detail)s") % {"detail": detail},
            )
            logger.error(f"[MEETING_MAIL_VIEW] manual send failed: {detail} (raw result={result})")

    except Exception as e:
        messages.error(
            request,
            _("⚠ メール送信中に例外が発生しました：%(error)s") % {"error": e},
        )
        logger.exception("[MEETING_MAIL_VIEW] EXCEPTION")

    return redirect("meetings:settings")
