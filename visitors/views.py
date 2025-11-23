from django.shortcuts import render, redirect, get_object_or_404
from django.forms import formset_factory
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import translation

from .forms import VisitorForm
from .models import Visitor, MailingAddress, HolidayDate, VisitMailConfig
from datetime import datetime, time, date

import os
import logging

from .email_utils import send_daily_email

logger = logging.getLogger(__name__)


CSV_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'visitor_list.csv')
MAILING_LIST_FILE = os.path.join(settings.BASE_DIR, 'visitors', 'mailing_list.csv')
HOLIDAYS_FILE = os.path.join(settings.BASE_DIR, 'holidays.csv')
SEND_TIME_FILE = os.path.join(settings.BASE_DIR, 'send_time.csv')


@login_required
def index(request):
    today = date.today()

    visitors_qs = Visitor.objects.filter(visit_date__gte=today).order_by("visit_date", "visit_time", "id")

    visitors = []
    for v in visitors_qs:
        visitors.append({
            "id": v.id,
            "visit_date": v.visit_date,
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
def set_language(request):
    lang_code = request.GET.get('language', 'ja')
    response = redirect(request.META.get('HTTP_REFERER', '/'))
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang_code)
    translation.activate(lang_code)
    return response


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
        visitor.cancelled = True
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
    """
    メール送信設定画面（SQL版）
    - メーリングリスト: MailingAddress
    - 休日設定: HolidayDate
    - 送信時刻・スケジューラ方式: VisitMailConfig
    """
    # 設定レコードを取得（なければ作成）
    config, _ = VisitMailConfig.objects.get_or_create(pk=1)

    if request.method == "POST":
        # ▼ スケジューラ方式
        scheduler_mode = request.POST.get("scheduler_mode", VisitMailConfig.MODE_WINDOWS)
        if scheduler_mode not in [
            VisitMailConfig.MODE_WINDOWS,
            VisitMailConfig.MODE_DJANGO,
            VisitMailConfig.MODE_NONE,
        ]:
            scheduler_mode = VisitMailConfig.MODE_WINDOWS

        # ▼ 送信時刻（時・分）
        send_hour_str = request.POST.get("send_hour", "09")
        send_minute_str = request.POST.get("send_minute", "00")

        try:
            send_hour = int(send_hour_str)
            send_minute = int(send_minute_str)
            send_time_value = time(send_hour, send_minute)
        except ValueError:
            send_time_value = time(9, 0)

        # ▼ メーリングリスト
        emails = [
            e.strip()
            for e in request.POST.getlist("emails")
            if e.strip()
        ]
        MailingAddress.objects.all().delete()
        for e in emails:
            MailingAddress.objects.create(email=e)

        # ▼ 休日設定
        holidays_input = [
            d.strip()
            for d in request.POST.getlist("holidays")
            if d.strip()
        ]
        HolidayDate.objects.all().delete()
        for d in holidays_input:
            try:
                # YYYY-MM-DD 形式としてパース
                dt = datetime.strptime(d, "%Y-%m-%d").date()
                HolidayDate.objects.create(date=dt)
            except ValueError:
                # 形式がおかしい行は無視
                continue

        # ▼ VisitMailConfig 保存
        config.send_time = send_time_value
        config.mode = scheduler_mode
        config.save()

        messages.success(request, "設定を保存しました。")
        return redirect("visitors:settings")

    # ====== GET 時の表示用データ ======

    # メーリングリスト
    mailing_list = list(
        MailingAddress.objects.values_list("email", flat=True)
    )

    # 休日一覧（文字列 YYYY-MM-DD）
    holidays = [
        h.date.strftime("%Y-%m-%d")
        for h in HolidayDate.objects.all().order_by("date")
    ]

    # 送信時刻
    if config.send_time:
        send_hour = f"{config.send_time.hour:02d}"
        send_minute = f"{config.send_time.minute:02d}"
    else:
        send_hour = "09"
        send_minute = "00"

    scheduler_mode = config.mode or VisitMailConfig.MODE_WINDOWS

    # ドロップダウン用の候補（0〜23時、00/15/30/45分）
    hours = [f"{h:02d}" for h in range(0, 24)]
    minutes = ["00", "15", "30", "45"]

    context = {
        "mailing_list": mailing_list,
        "holidays": holidays,
        "send_hour": send_hour,
        "send_minute": send_minute,
        "scheduler_mode": scheduler_mode,
        "hours": hours,
        "minutes": minutes,
    }
    return render(request, "visitors/settings.html", context)


@login_required
def run_email(request):
    """
    「今すぐ送信」ボタンから呼ばれるビュー。
    email_utils.send_daily_email() を呼び出し、
    結果をメッセージ & ログに出す。
    """
    try:
        result = send_daily_email()

        if result["sent"]:
            msg = f"📨 来訪予定メールを送信しました。（宛先: {len(result['recipients'])}件, 来訪件数: {result['visitor_count']}件）"
            messages.success(request, msg)
            logger.info(f"[VISITOR_MAIL_VIEW] {msg} recipients={result['recipients']}")
        else:
            msg = f"⚠ メールは送信されませんでした：{result['reason']}"
            messages.warning(request, msg)
            logger.warning(
                f"[VISITOR_MAIL_VIEW] {msg} "
                f"recipients={result['recipients']}, visitor_count={result['visitor_count']}"
            )

    except Exception as e:
        err_msg = f"⚠ メール送信中にエラーが発生しました：{e}"
        messages.error(request, err_msg)
        logger.error(f"[VISITOR_MAIL_VIEW] EXCEPTION: {e}", exc_info=True)

    return redirect('visitors:settings')
