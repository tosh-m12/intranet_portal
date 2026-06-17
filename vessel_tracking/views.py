"""本船動静管理 ビュー。全ビュー login_required。論理削除(is_cancelled)。"""
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .models import CONTAINER_TYPES, Shipment

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


def _shipper_choices():
    """荷主の入力候補 = billing(請求書管理台帳)取引先マスタの group_name(重複排除)。"""
    from billing.models import MasterParty
    return list(MasterParty.objects.values_list('group_name', flat=True)
                .distinct().order_by('group_name'))


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
    qs = Shipment.objects.filter(is_cancelled=False)
    customer = (request.GET.get('customer') or '').strip()
    dest = (request.GET.get('dest') or '').strip()
    status = (request.GET.get('status') or '').strip()
    delayed = (request.GET.get('delayed') or '').strip()
    q = (request.GET.get('q') or '').strip()

    if customer:
        qs = qs.filter(customer__icontains=customer)
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
        'shippers': _shipper_choices(),
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
    if not name:
        messages.error(request, _('荷主は必須です。'))
        return redirect('vessel_tracking:list')
    s = Shipment(customer=name, source='manual', created_by=request.user,
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
    messages.success(request, _('出荷を登録しました（%(customer)s / %(dest)s）。')
                     % {'customer': s.customer, 'dest': s.dest})
    return redirect('vessel_tracking:list')


# ---------------------------------------------------------------- 重複チェック(入力中ライブ警告)
@login_required
def dup_check(request):
    """重複候補の既存便を返す(JSON)。入力フォームの重複登録防止に使う。

    便の同一性は 荷主×本船×航海No。定期配船では同じ荷主×本船が航海違いで何度も並ぶため、
    そこまでを重複扱いにすると警告がノイズになる。よって航海No一致(未入力時は仕向地一致かつ
    双方航海No未入力)に絞り、本当の二重登録だけを警告する。登録自体は妨げない(人に確認させる)。
    """
    customer = (request.GET.get('customer') or '').strip()
    vessel = (request.GET.get('vessel') or '').strip()
    voyage = (request.GET.get('voyage') or '').strip()
    dest = (request.GET.get('dest') or '').strip()
    if not customer or not vessel:
        return JsonResponse({'matches': []})
    qs = (Shipment.objects.filter(is_cancelled=False,
                                  customer__iexact=customer, vessel__iexact=vessel)
          .order_by('-etd', '-id'))
    if voyage:
        qs = qs.filter(voyage__iexact=voyage)
    else:
        # 航海No未入力時は同定できないので、双方航海No空＋仕向地一致の場合のみ重複候補とする。
        qs = qs.filter(voyage='')
        if dest:
            qs = qs.filter(dest__iexact=dest)
        else:
            return JsonResponse({'matches': []})
    exclude = (request.GET.get('exclude') or '').strip()
    if exclude.isdigit():
        qs = qs.exclude(pk=int(exclude))
    matches = [{
        'pk': s.pk,
        'vessel': s.vessel,
        'voyage': s.voyage,
        'dest': s.dest,
        'etd': s.etd.strftime('%Y/%m/%d') if s.etd else '',
        'url': reverse('vessel_tracking:detail', args=[s.pk]),
    } for s in qs[:10]]
    return JsonResponse({'matches': matches})


# ---------------------------------------------------------------- ライブ監視
@login_required
def monitor(request):
    """就航中の本船ライブ動向(vessel_pro スナップショット)を一覧し、遅延予測を警告する。"""
    import datetime as _dt
    # 発地出発の遅延予測が主目的 → まだ出港していない便を監視対象にする。
    qs = (Shipment.objects.filter(is_cancelled=False, atd__isnull=True)
          .exclude(vessel=''))
    rows = list(qs)
    order = {'bad': 0, 'info': 1, 'ok': 2, 'muted': 3, 'none': 4}
    rows.sort(key=lambda s: (order.get(s.live_departure_predict[0], 9), s.etd or _dt.date.max))
    last = max((s.live_updated_at for s in rows if s.live_updated_at), default=None)
    # 最終更新は JST(+9) で表示(TIME_ZONE は Asia/Shanghai のため明示変換)。
    jst = _dt.timezone(_dt.timedelta(hours=9))
    last_jst = last.astimezone(jst).strftime('%m/%d %H:%M') if last else ''
    ctx = {
        'active_tab': 'monitor',
        'rows': rows,
        'count': len(rows),
        'alert_count': sum(1 for s in rows if s.live_departure_predict[0] == 'bad'),
        'last_updated': last,
        'last_updated_jst': last_jst,
    }
    return render(request, 'vessel_tracking/monitor.html', ctx)


@login_required
@require_POST
def monitor_refresh(request):
    """ライブ監視を手動で1回実行(track_vessels)。本番での動作確認・即時更新用。"""
    out = StringIO()
    try:
        call_command('track_vessels', stdout=out, stderr=out)
    except Exception as e:   # noqa: BLE001
        messages.error(request, _('ライブ更新に失敗しました: %(err)s') % {'err': e})
        return redirect('vessel_tracking:monitor')
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    summary = lines[-1].strip() if lines else ''
    messages.success(request, _('ライブ更新を実行しました。%(summary)s') % {'summary': summary})
    return redirect('vessel_tracking:monitor')


# ---------------------------------------------------------------- 詳細(読み取り専用)
@login_required
def detail(request, pk):
    obj = get_object_or_404(Shipment, pk=pk)
    return render(request, 'vessel_tracking/detail.html',
                  {'active_tab': 'list', 'obj': obj})


# ---------------------------------------------------------------- 入力/編集
@login_required
def entry(request, pk=None):
    obj = get_object_or_404(Shipment, pk=pk) if pk else None
    is_new = obj is None

    if request.method == 'POST':
        obj = obj or Shipment()
        name = (request.POST.get('customer') or '').strip()
        for fld in TEXT_FIELDS:
            setattr(obj, fld, (request.POST.get(fld) or '').strip())
        for fld in DATE_FIELDS:
            setattr(obj, fld, _date(request.POST.get(fld)))
        for fld in INT_FIELDS:
            setattr(obj, fld, _i(request.POST.get(fld)))
        for fld in FLOAT_FIELDS:
            setattr(obj, fld, _f(request.POST.get(fld)))
        obj.remarks = (request.POST.get('remarks') or '').strip()

        if not name:
            messages.error(request, _('荷主は必須です。'))
            return render(request, 'vessel_tracking/entry.html',
                          _entry_ctx(obj, is_new, request))
        obj.customer = name

        if is_new:
            obj.created_by = request.user
            obj.assignee = display_name(request.user)
        obj.save()
        messages.success(request, _('出荷トレーシングを保存しました（%(order)s / %(inv)s）。')
                         % {'order': obj.order_no, 'inv': obj.inv_no})
        if 'save_new' in request.POST:
            return redirect('vessel_tracking:entry')
        return redirect('vessel_tracking:detail', pk=obj.pk)

    return render(request, 'vessel_tracking/entry.html', _entry_ctx(obj, is_new, request))


def _entry_ctx(obj, is_new, request):
    return {
        'active_tab': 'entry',
        'obj': obj,
        'shippers': _shipper_choices(),
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
    messages.success(request, _('出荷トレーシングを取消しました。'))
    return redirect('vessel_tracking:list')
