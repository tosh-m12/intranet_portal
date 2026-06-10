"""請求書管理 ビュー。全ビュー login_required。論理削除(is_cancelled)。"""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import CURRENCIES, FEE_KEYS, FEES, InvoiceLine, MasterParty

# 入力フォームで扱うテキスト/整数フィールド(費目・合計を除く)
HEADER_TEXT = ['customer_gc', 'bill_to', 'bill_cat', 'currency', 'assignee', 'fx_currency']
HEADER_INT = ['bill_year', 'bill_month']
EXTRA_FLOAT = ['exrate']  # 為替レート(換算後金額は save() で自動計算)


def _r2(x):
    return round((x or 0) + 1e-9, 2)


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
def invoice_list(request):
    qs = InvoiceLine.objects.filter(is_cancelled=False)
    assignee = (request.GET.get('assignee') or '').strip()
    year = (request.GET.get('year') or '').strip()
    month = (request.GET.get('month') or '').strip()
    q = (request.GET.get('q') or '').strip()
    if assignee:
        qs = qs.filter(assignee=assignee)
    if year:
        qs = qs.filter(bill_year=year)
    if month:
        qs = qs.filter(bill_month=month)
    if q:
        qs = qs.filter(Q(bill_to__icontains=q) | Q(customer_gc__icontains=q)
                       | Q(serial__icontains=q))

    total = qs.aggregate(s=Sum('total_after_tax'))['s'] or 0
    assignees = [a for a in InvoiceLine.objects.filter(is_cancelled=False)
                 .values_list('assignee', flat=True).distinct().order_by('assignee') if a]
    years = [y for y in InvoiceLine.objects.filter(is_cancelled=False)
             .values_list('bill_year', flat=True).distinct().order_by('bill_year') if y]
    ctx = {
        'active_tab': 'list',
        'rows': qs[:500],
        'count': qs.count(),
        'total_after_tax': total,
        'assignees': assignees,
        'years': years,
        'f': {'assignee': assignee, 'year': year, 'month': month, 'q': q},
    }
    return render(request, 'billing/list.html', ctx)


# ---------------------------------------------------------------- 詳細表示(読み取り専用)
@login_required
def detail(request, pk):
    obj = get_object_or_404(InvoiceLine, pk=pk)
    fee_rows = []
    for k, label in FEES:
        net = getattr(obj, k)
        incl = obj.fee_incl(k) if net else None
        fee_rows.append({'label': label, 'net': net, 'rate': getattr(obj, f'{k}_rate'),
                         'tax': _r2(incl - net) if net else None, 'incl': incl})
    return render(request, 'billing/detail.html',
                  {'active_tab': 'list', 'obj': obj, 'fee_rows': fee_rows})


# ---------------------------------------------------------------- 入力/編集
@login_required
def entry(request, pk=None):
    obj = get_object_or_404(InvoiceLine, pk=pk) if pk else None
    is_new = obj is None

    if request.method == 'POST':
        obj = obj or InvoiceLine()
        for fld in HEADER_TEXT:
            setattr(obj, fld, (request.POST.get(fld) or '').strip())
        obj.customer_gc = obj.customer_gc.upper()
        obj.bill_cat = obj.bill_cat.upper()
        for fld in HEADER_INT:
            setattr(obj, fld, _i(request.POST.get(fld)))
        # 費目: 税抜チェック(既定オン)が外れた費目は税込入力扱い。DB には常に税抜額を保存。
        for k in FEE_KEYS:
            amt = _f(request.POST.get(k))
            rate = _f(request.POST.get(f'{k}_rate'))
            if not request.POST.get(f'{k}_net') and amt is not None and rate:
                amt = _r2(amt / (1 + rate / 100.0))
            setattr(obj, k, amt)
            setattr(obj, f'{k}_rate', rate)
        for fld in EXTRA_FLOAT:
            setattr(obj, fld, _f(request.POST.get(fld)))
        obj.rate_date = (request.POST.get('rate_date') or '').strip() or None
        if is_new:
            obj.created_by = request.user
        obj.save()
        messages.success(request, f'請求明細を保存しました（税込合計 {obj.total_after_tax:,.2f}）。')
        if 'save_new' in request.POST:
            return redirect('billing:entry')
        return redirect('billing:detail', pk=obj.pk)

    ctx = {
        'active_tab': 'entry',
        'obj': obj,
        'fees': FEES,
        'today': datetime.date.today().isoformat(),
        'currencies': CURRENCIES,
    }
    return render(request, 'billing/entry.html', ctx)


@login_required
@require_POST
def cancel(request, pk):
    obj = get_object_or_404(InvoiceLine, pk=pk)
    obj.is_cancelled = True
    obj.cancelled_at = timezone.now()
    obj.save(update_fields=['is_cancelled', 'cancelled_at', 'updated_at'])
    messages.success(request, '請求明細を取消しました。')
    return redirect('billing:list')


# ---------------------------------------------------------------- 取引先マスタ
@login_required
def master_list(request):
    q = (request.GET.get('q') or '').strip()
    qs = MasterParty.objects.all()
    if q:
        qs = qs.filter(Q(group_name__icontains=q) | Q(company_name__icontains=q)
                       | Q(assignee__icontains=q))
    return render(request, 'billing/master_list.html',
                  {'active_tab': 'master', 'rows': qs, 'count': qs.count(),
                   'total': MasterParty.objects.count(), 'q': q})


@login_required
def master_add(request):
    prefill_group = (request.GET.get('group') or '').strip()
    prefill_company = (request.GET.get('company') or '').strip()
    if request.method == 'POST':
        group = (request.POST.get('group_name') or '').strip().upper()
        company = (request.POST.get('company_name') or '').strip()
        assignee = (request.POST.get('assignee') or '').strip()
        if not group or not company:
            messages.error(request, 'グループ名と会社名は必須です。')
        elif MasterParty.objects.filter(group_name=group, company_name=company).exists():
            messages.error(request, f'「{group} / {company}」は既に登録済みです。')
        else:
            MasterParty.objects.create(group_name=group, company_name=company, assignee=assignee)
            messages.success(request, f'取引先「{group} / {company}」を登録しました。')
            nxt = request.POST.get('next')
            return redirect(nxt) if nxt else redirect('billing:master')
        prefill_group, prefill_company = group, company

    return render(request, 'billing/master_form.html', {
        'active_tab': 'master',
        'prefill_group': prefill_group,
        'prefill_company': prefill_company,
        'next': request.GET.get('next', ''),
        'groups': list(MasterParty.objects.values_list('group_name', flat=True)
                       .distinct().order_by('group_name')),
    })


# ---------------------------------------------------------------- JSON API
@login_required
def api_parties(request):
    q = (request.GET.get('q') or '').strip()
    field = request.GET.get('field', 'company')
    if not q:
        return JsonResponse({'items': []})
    if field == 'group':
        groups = (MasterParty.objects.filter(group_name__icontains=q)
                  .values_list('group_name', flat=True).distinct().order_by('group_name'))
        items = [{'value': g, 'label': g} for g in groups[:20]]
    else:
        qs = MasterParty.objects.filter(company_name__icontains=q)
        group = (request.GET.get('group') or '').strip()
        if group:
            scoped = qs.filter(group_name__iexact=group)
            if scoped.exists():
                qs = scoped
        # 会社名で重複排除する。同じ会社が複数グループに存在し、同名候補で20件が
        # 埋まって他社が出てこない問題を防ぐ。company_name で並ぶので各社の行は連続。
        seen, order = {}, []
        for p in qs.order_by('company_name', 'group_name'):
            e = seen.get(p.company_name)
            if e is None:
                if len(order) >= 20:
                    break
                seen[p.company_name] = {'group': p.group_name,
                                        'assignee': p.assignee,
                                        'groups': {p.group_name}}
                order.append(p.company_name)
            else:
                e['groups'].add(p.group_name)
        items = []
        for name in order:
            e = seen[name]
            multi = len(e['groups']) > 1   # 複数グループに跨る会社はグループを自動補完しない
            items.append({'value': name, 'label': name,
                          'group': '' if multi else e['group'],
                          'assignee': '' if multi else e['assignee']})
    return JsonResponse({'items': items})


@login_required
def api_check_company(request):
    company = (request.GET.get('company') or '').strip()
    group = (request.GET.get('group') or '').strip()
    if not company:
        return JsonResponse({'exists': None})
    if group:
        p = MasterParty.objects.filter(company_name__iexact=company,
                                       group_name__iexact=group).first()
        if p:
            return JsonResponse({'exists': True, 'group': p.group_name,
                                 'company': p.company_name, 'assignee': p.assignee})
    others = list(MasterParty.objects.filter(company_name__iexact=company)
                  .values_list('group_name', flat=True).distinct())
    if not group and len(others) == 1:
        p = MasterParty.objects.filter(company_name__iexact=company).first()
        return JsonResponse({'exists': True, 'group': p.group_name,
                             'company': p.company_name, 'assignee': p.assignee})
    return JsonResponse({'exists': False, 'other_groups': others})


@login_required
def api_next_serial(request):
    d = (request.GET.get('date') or '').strip() or None
    return JsonResponse({'next': InvoiceLine.next_serial(d)})
