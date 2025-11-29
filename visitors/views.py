from django.shortcuts import render, redirect, get_object_or_404
from django.forms import formset_factory
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.utils.timezone import localdate
from django.http import JsonResponse, HttpResponseBadRequest, Http404, HttpResponse
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.db import transaction

from .forms import VisitorForm
from .models import Visitor, MailingAddress, HolidayDate, VisitMailConfig

from datetime import date, time as dtime
import datetime
import json
import logging
import os
import csv
import io

from .email_utils import send_daily_email

logger = logging.getLogger(__name__)

# =========================================================
# 共通：Visitor の表示用 dict 変換
# =========================================================
def _serialize_visitor(v: Visitor):
    return {
        "id": v.id,
        # 表示用: 2025年11月04日
        "visit_date": v.visit_date.strftime("%Y年%m月%d日") if v.visit_date else "",
        # 編集用: 2025-11-04
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


# =========================================================
# 本日以降の一覧
# =========================================================
@login_required
def index(request):
    today = localdate()  # タイムゾーン考慮

    visitors_qs = Visitor.objects.filter(
        visit_date__gte=today
    ).order_by("visit_date", "visit_time", "id")

    visitors = [_serialize_visitor(v) for v in visitors_qs]

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

    visitors = []
    for v in visitors_qs:
        visitors.append({
            "id": v.id,
            # 表示用: 2025年11月04日
            "visit_date": v.visit_date.strftime("%Y年%m月%d日") if v.visit_date else "",
            # 編集用: 2025-11-04
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
        })

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
                    host_staff=data["host_staff"],
                    cancelled=False,
                )

            return redirect("visitors:index")

    return render(request, "visitors/add.html", {
        "formset": formset,
        "time_choices": time_choices,
    })


# =========================================================
# キャンセルフラグ ON/OFF
# =========================================================
@login_required
def cancel_visitor(request, id):
    if request.method != 'POST':
        return HttpResponseBadRequest("POST only")

    visitor = get_object_or_404(Visitor, pk=id)
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
# 個別編集（今後あまり使わないかも）
# =========================================================
@login_required
def edit_visitor(request, id):
    visitor = get_object_or_404(Visitor, pk=id)

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
            visitor.host_staff = data["host_staff"]

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
            "host_staff": visitor.host_staff,
        }
        form = VisitorForm(initial=initial)

    return render(request, "visitors/edit.html", {"form": form, "visitor_id": id})


# =========================================================
# メール設定画面
# =========================================================
@login_required
def settings_view(request):
    # VisitMailConfig は1レコードだけ使う想定
    config, _ = VisitMailConfig.objects.get_or_create(pk=1)

    if request.method == "POST":
        # ▼ スケジューラ方式
        mode = request.POST.get("scheduler_mode", VisitMailConfig.MODE_WINDOWS)
        if mode not in dict(VisitMailConfig.MODE_CHOICES):
            mode = VisitMailConfig.MODE_WINDOWS
        config.mode = mode

        # ▼ 送信時刻（時・分を別々に受け取る）
        send_hour_str = request.POST.get("send_hour", "09")
        send_minute_str = request.POST.get("send_minute", "00")
        try:
            send_hour = int(send_hour_str)
            send_minute = int(send_minute_str)
            config.send_time = dtime(send_hour, send_minute)
        except ValueError:
            config.send_time = dtime(9, 0)

        # ▼ SMTP 設定
        config.smtp_host = request.POST.get("smtp_host", "").strip() or "smtp.qiye.aliyun.com"
        try:
            config.smtp_port = int(request.POST.get("smtp_port", "587"))
        except ValueError:
            config.smtp_port = 587

        config.use_tls = bool(request.POST.get("use_tls"))
        config.use_ssl = bool(request.POST.get("use_ssl"))

        config.smtp_user = request.POST.get("smtp_user", "").strip()

        new_password = request.POST.get("smtp_password", "").strip()
        # パスワード欄が空なら「変更なし」
        if new_password:
            config.smtp_password = new_password

        config.from_name = request.POST.get("from_name", "").strip() or config.from_name

        # ▼ メーリングリスト再保存
        MailingAddress.objects.all().delete()
        emails = request.POST.getlist("emails")
        for e in emails:
            e = e.strip()
            if e:
                MailingAddress.objects.create(email=e)

        # ▼ 休日再保存
        HolidayDate.objects.all().delete()
        dates = request.POST.getlist("holidays")
        for d in dates:
            d = d.strip()
            if d:
                # "YYYY-MM-DD" 文字列をそのまま入れてOK（DateFieldが解釈）
                HolidayDate.objects.create(date=d)

        config.save()
        messages.success(request, "設定を保存しました。")
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
        "scheduler_mode": config.mode,
        "send_hour": f"{send_hour:02d}",
        "send_minute": f"{send_minute:02d}",
        "hours": hours,
        "minutes": minutes,
        "config": config,  # smtp_host 等をテンプレートから参照
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

        # ------------------------------------
        # 来訪日（YYYY-MM-DD）
        # ------------------------------------
        if field == "visit_date":
            try:
                dt = datetime.datetime.strptime(value, "%Y-%m-%d")
                v.visit_date = dt.date()
                v.save()
                display_value = f"{dt.year}年{dt.month:02}月{dt.day:02}日"
                return JsonResponse({"ok": True, "value": display_value})
            except Exception as e:
                return JsonResponse({"ok": False, "error": str(e)})

        # ------------------------------------
        # 来訪時間（HH:MM）
        # ------------------------------------
        elif field == "visit_time":
            try:
                # 空の場合（--:-- など）はエラーにしない
                if value == "":
                    v.visit_time = None
                else:
                    t = datetime.datetime.strptime(value, "%H:%M").time()
                    v.visit_time = t

                v.save()
                return JsonResponse({"ok": True, "value": value})
            except Exception as e:
                return JsonResponse({"ok": False, "error": str(e)})

        # ------------------------------------
        # 上記以外のフィールド（会社名・名前・目的など）
        # ------------------------------------
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

    # フラグを反転
    visitor.time_undecided = not visitor.time_undecided

    # 未定になったら時間をクリア
    if visitor.time_undecided:
        visitor.visit_time = None

    visitor.save()

    # Ajax の場合は JSON で返す
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            "ok": True,
            "time_undecided": visitor.time_undecided,
            "visit_time": visitor.visit_time.strftime("%H:%M") if visitor.visit_time else "",
        })

    # 通常遷移（保険）
    next_page = request.GET.get("next", "index")
    return redirect(f'visitors:{next_page}')


# =========================================================
# 今すぐメール送信
# =========================================================
@login_required
def run_email(request):
    try:
        # 1) メール送信実行
        result = send_daily_email()

        today = timezone.localdate()
        config, _ = VisitMailConfig.objects.get_or_create(pk=1)

        # 2) result の形式を吸収（dict でも str でも動くように）
        sent = False
        detail = ""

        if isinstance(result, dict):
            # 新仕様: {"sent": True/False, "reason": "...", "recipients": [...], ...}
            sent = result.get("sent", False)
            detail = result.get("reason", "")
        else:
            # 旧仕様: "ok" / "error: xxx"
            sent = (result == "ok")
            if not sent:
                detail = str(result)

        # 3) 成功・失敗で分岐
        if sent:
            # ★ 成功したときだけ「今日送った」と記録
            config.last_sent_date = today
            config.save(update_fields=["last_sent_date"])

            # 宛先表示（dict のときだけ）
            recipients_str = ""
            if isinstance(result, dict):
                recipients = result.get("recipients") or []
                if recipients:
                    recipients_str = " 宛先: " + ", ".join(recipients)

            messages.success(
                request,
                f"📨 メールを送信しました。{recipients_str}"
            )
            print(f"[VISITOR_MAIL_VIEW] manual send ok, last_sent_date={today}, result={result}")
        else:
            messages.error(
                request,
                f"⚠ メール送信に失敗しました。{detail}"
            )
            print(f"[VISITOR_MAIL_VIEW] manual send failed: {detail} (raw result={result})")

    except Exception as e:
        messages.error(request, f"⚠ メール送信中に例外が発生しました：{e}")
        print(f"[VISITOR_MAIL_VIEW] EXCEPTION: {e}")

    return redirect("visitors:settings")


@login_required
def download_settings_csv(request, kind):
    """
    設定画面からの CSV ダウンロード用。
    kind は 'visitor_list' のみ許可。
    DB の Visitor 全件を CSV で返す。
    """
    if kind != "visitor_list":
        raise Http404("Unknown CSV kind")

    # レスポンス準備
    response = HttpResponse(
        content_type="text/csv; charset=utf-8"
    )
    filename = f"visitor_list_{timezone.now().strftime('%Y%m%d')}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    # ヘッダー行
    writer.writerow([
        "id",
        "visit_date",       # YYYY-MM-DD
        "visit_time",       # HH:MM or 空欄
        "time_undecided",   # 1 or 0
        "company_name",
        "last_name",
        "first_name",
        "title",
        "purpose",
        "location",
        "host_staff",
        "cancelled",        # 1 or 0
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


@login_required
def upload_visitor_csv(request):
    """
    設定画面から Visitor 一覧 CSV をアップロードして DB をメンテナンスする用。
    - CSV の id カラムは「無視」して新規作成（PK 干渉防止）
    - アップロード成功時に Visitor 全件を入れ替える
    """
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    csv_file = request.FILES.get("csv_file")
    if not csv_file:
        messages.error(request, "CSVファイルが選択されていません。")
        return redirect("visitors:settings")

    try:
        # 文字コードは UTF-8 (BOM付きも許容)
        text_file = io.TextIOWrapper(csv_file.file, encoding="utf-8-sig")
        reader = csv.DictReader(text_file)

        new_visitors = []

        def to_bool(val):
            s = (val or "").strip().lower()
            return s in ("1", "true", "t", "yes", "y", "on")

        for row in reader:
            # CSVヘッダに存在しない場合に備えて get() で取得
            visit_date_str = (row.get("visit_date") or "").strip()
            visit_time_str = (row.get("visit_time") or "").strip()
            time_undecided_str = (row.get("time_undecided") or "").strip()
            cancelled_str = (row.get("cancelled") or "").strip()

            # 日付
            visit_date = None
            if visit_date_str:
                # "YYYY-MM-DD" 想定
                visit_date = datetime.datetime.strptime(visit_date_str, "%Y-%m-%d").date()

            # 時間
            visit_time_str = visit_time_str.strip()
            if not visit_time_str:
                visit_time = None
            else:
                # 秒付き(HH:MM:SS)にも対応
                try:
                    visit_time = datetime.datetime.strptime(visit_time_str, "%H:%M").time()
                except ValueError:
                    visit_time = datetime.datetime.strptime(visit_time_str, "%H:%M:%S").time()

            time_undecided = to_bool(time_undecided_str)
            cancelled = to_bool(cancelled_str)

            # 「未定」の場合は visit_time を強制的に None に
            if time_undecided:
                visit_time = None

            v = Visitor(
                # id はセットしない → DB が新しく採番する
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

        # ここまで読み込み成功したら、トランザクションで入れ替え
        with transaction.atomic():
            Visitor.objects.all().delete()
            Visitor.objects.bulk_create(new_visitors)

        messages.success(request, f"VisitorデータをCSVから再登録しました（{len(new_visitors)}件）。")

    except Exception as e:
        logger.exception("upload_visitor_csv error")
        messages.error(request, f"CSVの読み込み中にエラーが発生しました: {e}")

    return redirect("visitors:settings")
