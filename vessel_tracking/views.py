"""本船動向管理 ビュー。全ビュー login_required。論理削除(is_cancelled)。"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import CONTAINER_TYPES, Customer, Shipment

# 画面フォームで扱うフィールド分類。assignee はログインユーザーから自動設定する。
TEXT_FIELDS = ['order_no', 'inv_no', 'origin', 'dest', 'container_type',
               'vessel', 'voyage']
DATE_FIELDS = ['order_received', 'etd', 'cy_cut', 'cy_open', 'cy_open_act',
               'vanning', 'eta', 'atd', 'ata', 'shanghai_eta']
INT_FIELDS = ['out_ctn', 'out_qty']
FLOAT_FIELDS = ['out_m3']

STATUS_CHOICES = [
    ('received', '受注'),
    ('booked', 'ブッキング確定'),
    ('departed', '出港済'),
    ('arrived', '入港済'),
    ('cancelled', '取消'),
]


def display_name(user):
    """担当者として記録する表示名。氏名(姓+名) 優先、無ければメール。"""
    name = f'{user.last_name or ""}{user.first_name or ""}'.strip()
    return name or (user.email or '')


def _date(v):
    return (v or '').strip() or None


def _f(v):
    if v is None:
        return None
    v = str(v).strip().replace(',', '')
    if v == '':
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _i(v):
    f = _f(v)
    return int(f) if f is not None else None


# ---------------------------------------------------------------- 一覧
@login_required
def shipment_list(request):
    qs = Shipment.objects.filter(is_cancelled=False).select_related('customer')
    customer = (request.GET.get('customer') or '').strip()
    dest = (request.GET.get('dest') or '').strip()
    status = (request.GET.get('status') or '').strip()
    delayed = (request.GET.get('delayed') or '').strip()
    q = (request.GET.get('q') or '').strip()

    if customer:
        qs = qs.filter(customer_id=customer)
    if dest:
        qs = qs.filter(dest__iexact=dest)
    if q:
        qs = qs.filter(Q(order_no__icontains=q) | Q(inv_no__icontains=q)
                       | Q(vessel__icontains=q) | Q(voyage__icontains=q))

    rows = list(qs[:500])
    # 状況・遅延はモデルプロパティ(算出値)なので Python 側で絞り込む。
    if status:
        rows = [r for r in rows if r.status_key == status]
    if delayed:
        rows = [r for r in rows if r.is_delayed]

    ctx = {
        'active_tab': 'list',
        'rows': rows,
        'count': len(rows),
        'customers': Customer.objects.filter(is_active=True),
        'dests': [d for d in Shipment.objects.filter(is_cancelled=False)
                  .values_list('dest', flat=True).distinct().order_by('dest') if d],
        'status_choices': STATUS_CHOICES,
        'f': {'customer': customer, 'dest': dest, 'status': status,
              'delayed': delayed, 'q': q},
        # 簡易入力フォーム用
        'quick_container_types': ['20F', '40F', 'LCL'],
        'auto_assignee': display_name(request.user),
    }
    return render(request, 'vessel_tracking/list.html', ctx)


# ---------------------------------------------------------------- 簡易登録(一覧上部のインラインフォーム)
@login_required
@require_POST
def quick_create(request):
    name = (request.POST.get('customer') or '').strip()
    cust = (Customer.objects.filter(Q(name__iexact=name) | Q(code__iexact=name)).first()
            if name else None)
    if not cust:
        messages.error(request, '荷主は荷主マスタから選択してください。')
        return redirect('vessel_tracking:list')
    s = Shipment(customer=cust, source='manual', created_by=request.user,
                 assignee=display_name(request.user))
    s.origin = (request.POST.get('origin') or '').strip()
    s.dest = (request.POST.get('dest') or '').strip()
    s.container_type = (request.POST.get('container_type') or '').strip()
    s.out_m3 = _f(request.POST.get('out_m3'))
    # LCL はコンテナ本数なし(フォームでもグレーアウト)。
    if s.container_type.upper() != 'LCL':
        s.container_count = _i(request.POST.get('container_count'))
    s.vessel = (request.POST.get('vessel') or '').strip()
    s.voyage = (request.POST.get('voyage') or '').strip()
    s.cy_open = _date(request.POST.get('cy_open'))       # 予想CY Open(ECYオープン予定)
    s.etd = _date(request.POST.get('etd'))
    s.eta = _date(request.POST.get('eta'))
    s.save()
    messages.success(request, f'出荷を登録しました（{cust.name} / {s.dest}）。')
    return redirect('vessel_tracking:list')


# ---------------------------------------------------------------- ライブ監視
@login_required
def monitor(request):
    """就航中の本船ライブ動向(vessel_pro スナップショット)を一覧し、遅延予測を警告する。

    対象 = 取消でない・本船名あり・着地未確定(ata 未入力)の便。
    ライブ値は track_vessels コマンドが更新する。
    """
    import datetime as _dt
    # 発地出発の遅延予測が主目的 → まだ出港していない便を監視対象にする。
    qs = (Shipment.objects.filter(is_cancelled=False, atd__isnull=True)
          .exclude(vessel='').select_related('customer'))
    rows = list(qs)
    order = {'bad': 0, 'info': 1, 'ok': 2, 'muted': 3, 'none': 4}
    rows.sort(key=lambda s: (order.get(s.live_departure_predict[0], 9), s.etd or _dt.date.max))
    last = max((s.live_updated_at for s in rows if s.live_updated_at), default=None)
    ctx = {
        'active_tab': 'monitor',
        'rows': rows,
        'count': len(rows),
        'alert_count': sum(1 for s in rows if s.live_departure_predict[0] == 'bad'),
        'last_updated': last,
    }
    return render(request, 'vessel_tracking/monitor.html', ctx)


# ---------------------------------------------------------------- 詳細(読み取り専用)
@login_required
def detail(request, pk):
    obj = get_object_or_404(Shipment.objects.select_related('customer'), pk=pk)
    return render(request, 'vessel_tracking/detail.html',
                  {'active_tab': 'list', 'obj': obj})


# ---------------------------------------------------------------- 入力/編集
@login_required
def entry(request, pk=None):
    obj = get_object_or_404(Shipment, pk=pk) if pk else None
    is_new = obj is None

    if request.method == 'POST':
        obj = obj or Shipment()
        # 荷主は荷主マスタからのみ選択可(自由入力不可)。未選択・不正値は弾く。
        cust_id = (request.POST.get('customer') or '').strip()
        cust = Customer.objects.filter(pk=cust_id).first() if cust_id else None
        for fld in TEXT_FIELDS:
            setattr(obj, fld, (request.POST.get(fld) or '').strip())
        for fld in DATE_FIELDS:
            setattr(obj, fld, _date(request.POST.get(fld)))
        for fld in INT_FIELDS:
            setattr(obj, fld, _i(request.POST.get(fld)))
        for fld in FLOAT_FIELDS:
            setattr(obj, fld, _f(request.POST.get(fld)))
        obj.remarks = (request.POST.get('remarks') or '').strip()

        if not cust:
            messages.error(request, '荷主は荷主マスタから選択してください。')
            return render(request, 'vessel_tracking/entry.html',
                          _entry_ctx(obj, is_new, request))
        obj.customer = cust

        if is_new:
            obj.created_by = request.user
            obj.assignee = display_name(request.user)
        obj.save()
        messages.success(request, f'出荷トレーシングを保存しました（{obj.order_no} / {obj.inv_no}）。')
        if 'save_new' in request.POST:
            return redirect('vessel_tracking:entry')
        return redirect('vessel_tracking:detail', pk=obj.pk)

    return render(request, 'vessel_tracking/entry.html', _entry_ctx(obj, is_new, request))


def _entry_ctx(obj, is_new, request):
    return {
        'active_tab': 'entry',
        'obj': obj,
        'customers': Customer.objects.filter(is_active=True),
        'container_types': CONTAINER_TYPES,
        'dests': [d for d in Shipment.objects.values_list('dest', flat=True)
                  .distinct().order_by('dest') if d],
        'auto_assignee': (obj.assignee if obj else '') or display_name(request.user),
    }


@login_required
@require_POST
def cancel(request, pk):
    obj = get_object_or_404(Shipment, pk=pk)
    obj.is_cancelled = True
    obj.cancelled_at = timezone.now()
    obj.save(update_fields=['is_cancelled', 'cancelled_at', 'updated_at'])
    messages.success(request, '出荷トレーシングを取消しました。')
    return redirect('vessel_tracking:list')


# ---------------------------------------------------------------- 荷主マスタ
@login_required
def customer_list(request):
    q = (request.GET.get('q') or '').strip()
    qs = Customer.objects.all()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))
    return render(request, 'vessel_tracking/customer_list.html',
                  {'active_tab': 'customers', 'rows': qs, 'count': qs.count(),
                   'total': Customer.objects.count(), 'q': q})


@login_required
def customer_add(request):
    prefill_code = (request.GET.get('code') or '').strip()
    prefill_name = (request.GET.get('name') or '').strip()
    if request.method == 'POST':
        code = (request.POST.get('code') or '').strip().upper()
        name = (request.POST.get('name') or '').strip()
        if not code or not name:
            messages.error(request, '荷主コードと荷主名は必須です。')
        elif Customer.objects.filter(code=code).exists():
            messages.error(request, f'荷主コード「{code}」は既に登録済みです。')
        else:
            Customer.objects.create(code=code, name=name)
            messages.success(request, f'荷主「{code} / {name}」を登録しました。')
            nxt = request.POST.get('next')
            return redirect(nxt) if nxt else redirect('vessel_tracking:customers')
        prefill_code, prefill_name = code, name

    return render(request, 'vessel_tracking/customer_form.html', {
        'active_tab': 'customers',
        'prefill_code': prefill_code,
        'prefill_name': prefill_name,
        'next': request.GET.get('next', ''),
    })


# ---------------------------------------------------------------- JSON API
@login_required
def api_customers(request):
    q = (request.GET.get('q') or '').strip()
    qs = Customer.objects.filter(is_active=True)
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))
    items = [{'value': c.pk, 'label': c.name, 'group': c.code} for c in qs[:20]]
    return JsonResponse({'items': items})
