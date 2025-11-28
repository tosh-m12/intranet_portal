from django.shortcuts import render, redirect, get_object_or_404
from django.forms import formset_factory
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import translation, timezone
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST

from .forms import VisitorForm
from .models import Visitor, MailingAddress, HolidayDate, VisitMailConfig
from datetime import datetime, date, time as dtime

import json
import os
import logging

from .email_utils import send_daily_email

logger = logging.getLogger(__name__)


@login_required
def index(request):
    today = date.today()

    visitors_qs = Visitor.objects.filter(
        visit_date__gte=today
    ).order_by("visit_date", "visit_time", "id")

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
            "notes": v.notes,
            "cancelled_flag": v.cancelled,
        })

    return render(request, 'visitors/index.html', {'visitors': visitors})


@login_required
def history(request):
    """過去の来訪者一覧（本日より前）"""
    today = timezone.localdate()
    visitors = (
        Visitor.objects
        .filter(visit_date__lt=today)
        .order_by('-visit_date', '-visit_time', 'company_name')  # 過去なので日付降順
    )
    return render(request, "visitors/history.html", {"visitors": visitors})

@login_required
def add_visitor(request):
    VisitorFormSet = formset_factory(VisitorForm, extra=3)
    formset = VisitorFormSet(request.POST or None)
    time_choices = formset.empty_form.fields['visit_time'].widget.choices

    if request.method == 'POST':
        has_error = False
        valid_forms = []

        for form in formset:
            # 全項目空欄ならスキップ
            all_empty = all(
                not form.data.get(f'{form.prefix}-{field}')
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
                    visit_date=data['visit_date'],
                    visit_time=data['visit_time'] if not data.get('time_undecided') else None,
                    time_undecided=data.get('time_undecided', False),
                    company_name=data['company_name'],
                    last_name=data['last_name'],
                    first_name=data['first_name'],
                    title=data.get('title', ''),
                    purpose=data.get('purpose', ''),
                    location=data['location'],
                    host_staff=data['host_staff'],
                    notes=data.get('notes', ''),
                    cancelled=False,
                )

            return redirect('visitors:index')

    return render(request, 'visitors/add.html', {
        'formset': formset,
        'time_choices': time_choices,
    })


@login_required
def cancel_visitor(request, id):
    if request.method == 'POST':
        visitor = get_object_or_404(Visitor, pk=id)
        # 現在のフラグを反転させる（True→False, False→True）
        visitor.cancelled = not visitor.cancelled
        visitor.save()

    return redirect('visitors:index')


@login_required
def edit_visitor(request, id):
    visitor = get_object_or_404(Visitor, pk=id)

    if request.method == 'POST':
        form = VisitorForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data

            visitor.visit_date = data['visit_date']
            visitor.visit_time = data['visit_time'] if not data.get('time_undecided') else None
            visitor.time_undecided = data.get('time_undecided', False)
            visitor.company_name = data['company_name']
            visitor.last_name = data['last_name']
            visitor.first_name = data['first_name']
            visitor.title = data.get('title', '')
            visitor.purpose = data.get('purpose', '')
            visitor.location = data['location']
            visitor.host_staff = data['host_staff']
            visitor.notes = data.get('notes', '')

            visitor.save()
            return redirect('visitors:index')
    else:
        initial = {
            'visit_date': visitor.visit_date,
            'visit_time': visitor.visit_time,
            'time_undecided': visitor.time_undecided,
            'company_name': visitor.company_name,
            'last_name': visitor.last_name,
            'first_name': visitor.first_name,
            'title': visitor.title,
            'purpose': visitor.purpose,
            'location': visitor.location,
            'host_staff': visitor.host_staff,
            'notes': visitor.notes,
        }
        form = VisitorForm(initial=initial)

    return render(request, 'visitors/edit.html', {'form': form, 'visitor_id': id})


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


@require_POST
@login_required
def inline_update(request):
    import datetime
    import json
    from django.http import JsonResponse
    from .models import Visitor

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



@login_required
def toggle_undecided(request, id):
    if request.method == 'POST':
        visitor = get_object_or_404(Visitor, pk=id)
        visitor.time_undecided = not visitor.time_undecided

        # 未定になったら時間をクリア
        if visitor.time_undecided:
            visitor.visit_time = None

        visitor.save()

    return redirect('visitors:index')



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

    return redirect('visitors:settings')
