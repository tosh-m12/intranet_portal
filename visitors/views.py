from django.shortcuts import render, redirect, get_object_or_404
from django.forms import formset_factory
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.utils.timezone import localdate
from django.http import JsonResponse, HttpResponseBadRequest, Http404, HttpResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from .forms import VisitorForm
from .models import Visitor, MailingAddress, VisitMailConfig
from working_schedule.models import HolidayDate
from .email_utils import send_daily_email, get_visitors_for_mail

import datetime
import json
import logging
import csv
import io
from datetime import time as dtime

logger = logging.getLogger(__name__)


def is_admin(user):
    return user.is_superuser or user.is_staff


def can_edit_visitor(user, visitor: Visitor) -> bool:
    """
    ・管理者（superuser / staff）は常にOK
    ・それ以外は、作成者（created_by == user）のみOK
    ・created_by が None の場合は、管理者のみ編集可
    """
    if not user.is_authenticated:
        return False

    if is_admin(user):
        return True

    if visitor.created_by_id is None:
        # 古いデータなど、入力者不明 → 一般ユーザーは編集不可
        return False

    return visitor.created_by_id == user.id

def _get_display_name(user):
    """
    入力者名を「姓 名」の順で返す。
    姓・名が両方空なら username を返す。
    """
    last = (getattr(user, "last_name", "") or "").strip()
    first = (getattr(user, "first_name", "") or "").strip()

    if last or first:
        return (last + " " + first).strip()
    return user.get_username()

# =========================================================
# 共通：Visitor の表示用 dict 変換
# =========================================================
def _serialize_visitor(v: Visitor, user=None):
    data = {
        "id": v.id,
        "visit_date": v.visit_date.strftime("%Y年%m月%d日") if v.visit_date else "",
        "visit_date_raw": v.visit_date.strftime("%Y-%m-%d") if v.visit_date else "",
        "visit_time": v.visit_time.strftime("%H:%M") if v.visit_time else "",
        "time_undecided_flag": v.time_undecided,
        "company_name": v.company_name,
        "last_name": v.last_name,
        "first_name": v.first_name,
        "title": v.title,
        "purpose": v.purpose,
        "location": v.location,
        "host_staff": v.host_staff,
        "cancelled_flag": v.cancelled,
    }

    if user is not None:
        data["can_edit"] = can_edit_visitor(user, v)

    return data

# =========================================================
# 本日以降の一覧
# =========================================================
@login_required
def index(request):
    today = localdate()  # タイムゾーン考慮

    visitors_qs = Visitor.objects.filter(
        visit_date__gte=today
    ).order_by("visit_date", "visit_time", "id")

    visitors = [_serialize_visitor(v, request.user) for v in visitors_qs]

    return render(request, "visitors/index.html", {"visitors": visitors})


# =========================================================
# 過去の来訪者一覧（本日を含む過去）
# =========================================================
@login_required
def history(request):
    today = localdate()

    visitors_qs = Visitor.objects.filter(
        visit_date__lte=today
    ).order_by("-visit_date", "visit_time", "id")

    visitors = [_serialize_visitor(v, request.user) for v in visitors_qs]

    return render(request, "visitors/history.html", {
        "visitors": visitors,
    })


# =========================================================
# 新規登録
# =========================================================
@login_required
def add_visitor(request):
    VisitorFormSet = formset_factory(VisitorForm, extra=3)
    formset = VisitorFormSet(request.POST or None)
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

                Visitor.objects.create(
                    visit_date=data["visit_date"],
                    visit_time=data["visit_time"] if not data.get("time_undecided") else None,
                    time_undecided=data.get("time_undecided", False),
                    company_name=data["company_name"],
                    last_name=data["last_name"],
                    first_name=data["first_name"],
                    title=data.get("title", ""),
                    purpose=data.get("purpose", ""),
                    location=data["location"],
                    # 画面表示用：フルネーム
                    host_staff=_get_display_name(request.user),
                    cancelled=False,
                    # ★権限判定用：ID
                    created_by=request.user,
                )

            return redirect("visitors:index")

    return render(request, "visitors/add.html", {
        "formset": formset,
        "time_choices": time_choices,
        "host_name": host_name, 
    })


# =========================================================
# キャンセルフラグ ON/OFF
# =========================================================
@login_required
def cancel_visitor(request, id):
    if request.method != 'POST':
        return HttpResponseBadRequest("POST only")

    visitor = get_object_or_404(Visitor, pk=id)

    # ★権限チェック
    if not can_edit_visitor(request.user, visitor):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse(
                {"ok": False, "error": _("この来訪予定を編集する権限がありません。")},
                status=403,
            )
        return HttpResponseForbidden(_("この来訪予定を編集する権限がありません。"))

    visitor.cancelled = not visitor.cancelled
    visitor.save()    

    # Ajax の場合は JSON で返す
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            "ok": True,
            "cancelled": visitor.cancelled,
        })

    # 通常遷移（保険）
    next_page = request.GET.get("next", "index")
    return redirect(f'visitors:{next_page}')


# =========================================================
# 個別編集
# =========================================================
@login_required
def edit_visitor(request, id):
    visitor = get_object_or_404(Visitor, pk=id)

    # ★権限チェック：本人 or 管理権限者のみ
    if not can_edit_visitor(request.user, visitor):
        return HttpResponseForbidden(_("この来訪予定を編集する権限がありません。"))

    if request.method == "POST":
        form = VisitorForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data

            visitor.visit_date = data["visit_date"]
            visitor.visit_time = data["visit_time"] if not data.get("time_undecided") else None
            visitor.time_undecided = data.get("time_undecided", False)
            visitor.company_name = data["company_name"]
            visitor.last_name = data["last_name"]
            visitor.first_name = data["first_name"]
            visitor.title = data.get("title", "")
            visitor.purpose = data.get("purpose", "")
            visitor.location = data["location"]
            # ★host_staff は「入力者」として固定。編集時は触らない。
            # visitor.host_staff = data["host_staff"]

            visitor.save()
            return redirect("visitors:index")
    else:
        initial = {
            "visit_date": visitor.visit_date,
            "visit_time": visitor.visit_time,
            "time_undecided": visitor.time_undecided,
            "company_name": visitor.company_name,
            "last_name": visitor.last_name,
            "first_name": visitor.first_name,
            "title": visitor.title,
            "purpose": visitor.purpose,
            "location": visitor.location,
            # ★フォームで編集させないなら initial にも渡さない
            # "host_staff": visitor.host_staff,
        }
        form = VisitorForm(initial=initial)

    return render(request, "visitors/edit.html", {"form": form, "visitor_id": id})


# =========================================================
# メール設定画面
#   - SMTP 設定は mailcenter に移管済みなのでここでは扱わない
#   - スケジューラー方式も Django 内部固定。mode は常に MODE_DJANGO にしておく。
# =========================================================
@user_passes_test(is_admin)
@login_required
def settings_view(request):
    # VisitMailConfig は1レコードだけ使う想定
    config, _ = VisitMailConfig.objects.get_or_create(pk=1)

    if request.method == "POST":
        # ▼ 送信時刻（時・分を別々に受け取る）
        send_hour_str = request.POST.get("send_hour", "09")
        send_minute_str = request.POST.get("send_minute", "00")
        try:
            send_hour = int(send_hour_str)
            send_minute = int(send_minute_str)
            config.send_time = dtime(send_hour, send_minute)
        except ValueError:
            config.send_time = dtime(9, 0)

        # 自動送信方式は Django 内部固定にする
        config.mode = VisitMailConfig.MODE_DJANGO

        # ▼ メーリングリスト再保存
        MailingAddress.objects.all().delete()
        emails = request.POST.getlist("emails")
        for e in emails:
            e = e.strip()
            if e:
                MailingAddress.objects.create(email=e)

        # ▼ 休日再保存（working_schedule 側の HolidayDate をここから編集）
        HolidayDate.objects.all().delete()
        dates = request.POST.getlist("holidays")
        for d in dates:
            d = d.strip()
            if d:
                HolidayDate.objects.create(date=d)

        config.save()
        messages.success(request, _("設定を保存しました。"))
        return redirect("visitors:settings")

    # ===== GET（画面表示） =====
    mailing_list = list(MailingAddress.objects.values_list("email", flat=True))
    holidays = list(HolidayDate.objects.values_list("date", flat=True))

    # 時刻を分解
    send_hour = config.send_time.hour if config.send_time else 9
    send_minute = config.send_time.minute if config.send_time else 0

    # 時・分の候補（テンプレートの hours / minutes 用）
    hours = [f"{h:02d}" for h in range(0, 24)]
    minutes = [f"{m:02d}" for m in (0, 15, 30, 45)]

    context = {
        "mailing_list": mailing_list,
        "holidays": [d.strftime("%Y-%m-%d") for d in holidays],
        "send_hour": f"{send_hour:02d}",
        "send_minute": f"{send_minute:02d}",
        "hours": hours,
        "minutes": minutes,
        "config": config,
    }
    return render(request, "visitors/settings.html", context)


# =========================================================
# 一覧のインライン更新（AJAX）
# =========================================================
@require_POST
@login_required
def inline_update(request):
    try:
        data = json.loads(request.body)
        visitor_id = data.get("id")
        field = data.get("field")
        value = data.get("value", "").strip()

        v = Visitor.objects.get(id=visitor_id)

        # ★権限チェック
        if not can_edit_visitor(request.user, v):
            return JsonResponse(
                {"ok": False, "error": _("この来訪予定を編集する権限がありません。")},
                status=403,
            )

        # 来訪日（YYYY-MM-DD）
        if field == "visit_date":
            try:
                dt = datetime.datetime.strptime(value, "%Y-%m-%d")
                v.visit_date = dt.date()
                v.save()
                display_value = f"{dt.year}年{dt.month:02}月{dt.day:02}日"
                return JsonResponse({"ok": True, "value": display_value})
            except Exception as e:
                return JsonResponse({"ok": False, "error": str(e)})

        # 来訪時間（HH:MM）
        elif field == "visit_time":
            try:
                if value == "":
                    v.visit_time = None
                else:
                    t = datetime.datetime.strptime(value, "%H:%M").time()
                    v.visit_time = t

                v.save()
                return JsonResponse({"ok": True, "value": value})
            except Exception as e:
                return JsonResponse({"ok": False, "error": str(e)})

        # 上記以外（会社名・名前・目的など）
        else:
            setattr(v, field, value)
            v.save()
            display_value = value if value is not None else ""
            return JsonResponse({"ok": True, "value": display_value})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


# =========================================================
# 「時間未定」トグル
# =========================================================
@login_required
def toggle_undecided(request, id):
    if request.method != 'POST':
        return HttpResponseBadRequest("POST only")

    visitor = get_object_or_404(Visitor, pk=id)

    # ★権限チェック
    if not can_edit_visitor(request.user, visitor):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse(
                {"ok": False, "error": _("この来訪予定を編集する権限がありません。")},
                status=403,
            )
        return HttpResponseForbidden(_("この来訪予定を編集する権限がありません。"))

    visitor.time_undecided = not visitor.time_undecided

    if visitor.time_undecided:
        visitor.visit_time = None

    visitor.save()

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            "ok": True,
            "time_undecided": visitor.time_undecided,
            "visit_time": visitor.visit_time.strftime("%H:%M") if visitor.visit_time else "",
        })

    next_page = request.GET.get("next", "index")
    return redirect(f'visitors:{next_page}')


# =========================================================
# メールプレビュー画面
# =========================================================
@login_required
def preview_email(request):
    # 今日以降のデータを取得（本番のメールと同じロジック）
    today = localdate()
    visitors = get_visitors_for_mail(today)

    # メール本文（HTML）をテンプレートから生成
    html_body = render_to_string(
        "visitors/email_template.html",
        {
            "visitors": visitors,
            "today": today,
        },
    )

    # portal のレイアウトに埋め込んで表示
    return render(
        request,
        "visitors/email_preview.html",
        {
            "email_html": mark_safe(html_body),
        },
    )


# =========================================================
# 今すぐメール送信（手動）
# =========================================================
@login_required
def run_email(request):
    try:
        # ★ 手動送信なので ignore_holiday=True
        result = send_daily_email(ignore_holiday=True)

        today = timezone.localdate()
        config, _ = VisitMailConfig.objects.get_or_create(pk=1)

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
                    recipients_str = " 宛先: " + ", ".join(recipients)

            messages.success(
                request,
                _("📨 メールを送信しました。%(recipients)s") % {"recipients": recipients_str}
            )
            print(f"[VISITOR_MAIL_VIEW] manual send ok, last_sent_date={today}, result={result}")
        else:
            messages.error(
                request,
                _("⚠ メール送信に失敗しました。%(detail)s") % {"detail": detail}
            )
            print(f"[VISITOR_MAIL_VIEW] manual send failed: {detail} (raw result={result})")

    except Exception as e:
        messages.error(request, _("⚠ メール送信中に例外が発生しました：%(error)s") % {"error": e})
        print(f"[VISITOR_MAIL_VIEW] EXCEPTION: {e}")

    return redirect("visitors:settings")


# =========================================================
# 設定画面：Visitor 一覧 CSV ダウンロード
# =========================================================
@login_required
def download_settings_csv(request, kind):
    if kind != "visitor_list":
        raise Http404("Unknown CSV kind")

    response = HttpResponse(
        content_type="text/csv; charset=utf-8"
    )
    filename = f"visitor_list_{timezone.now().strftime('%Y%m%d')}.csv"
    response["Content-Disposition"] = f"attachment; filename=\"{filename}\""

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

    qs = Visitor.objects.all().order_by("visit_date", "visit_time", "id")
    for v in qs:
        visit_date = v.visit_date.strftime("%Y-%m-%d") if v.visit_date else ""
        visit_time = v.visit_time.strftime("%H:%M") if v.visit_time else ""
        time_undecided = "1" if v.time_undecided else "0"
        cancelled = "1" if v.cancelled else "0"

        writer.writerow([
            v.id,
            visit_date,
            visit_time,
            time_undecided,
            v.company_name,
            v.last_name,
            v.first_name,
            v.title,
            v.purpose,
            v.location,
            v.host_staff,
            cancelled,
        ])

    return response


# =========================================================
# 設定画面：Visitor 一覧 CSV アップロード
# =========================================================
@login_required
def upload_visitor_csv(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    csv_file = request.FILES.get("csv_file")
    if not csv_file:
        messages.error(request, _("CSVファイルが選択されていません。"))
        return redirect("visitors:settings")

    try:
        text_file = io.TextIOWrapper(csv_file.file, encoding="utf-8-sig")
        reader = csv.DictReader(text_file)

        new_visitors = []

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
                    visit_time = datetime.datetime.strptime(visit_time_str, "%H:%M:%S").time()

            time_undecided = to_bool(time_undecided_str)
            cancelled = to_bool(cancelled_str)

            if time_undecided:
                visit_time = None

            v = Visitor(
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
            new_visitors.append(v)

        with transaction.atomic():
            Visitor.objects.all().delete()
            Visitor.objects.bulk_create(new_visitors)

        messages.success(request, _("VisitorデータをCSVから再登録しました（%(count)s件）。") % {"count": len(new_visitors)})

    except Exception as e:
        logger.exception("upload_visitor_csv error")
        messages.error(request, _("CSVの読み込み中にエラーが発生しました: %(error)s") % {"error": e})

    return redirect("visitors:settings")

