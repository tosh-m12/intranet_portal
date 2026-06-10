from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import render

from billing.models import InvoiceLine
from .chartdata import SERIES, build_customer_payload, build_payload, period_totals
from .models import Customer, MonthlySale


@login_required
def index(request):
    return render(request, 'sales_trend/index.html')


@login_required
def partner_overview(request):
    """荷主グループの「XXXX ビジネス概要」。?group=<group_name>。

    現時点の portal DB(sales_trend + billing)にある情報を表示。DBに無い項目は
    「未入力」で枠だけ用意(今後担当者が入力)。group 名は請求台帳の基準名に統一済み。
    """
    group = (request.GET.get('group') or '').strip()
    customers = list(Customer.objects.filter(group_name=group).order_by('code'))

    sale_rows = list(MonthlySale.objects.filter(customer__group_name=group)
                     .values_list('year', 'month', 'amount'))
    chart = period_totals(sale_rows)

    # 前年度(=データ最新年の前年=直近の通年)の売上総額・月額平均。
    max_year = MonthlySale.objects.aggregate(m=Max('year'))['m']
    prev_year = (max_year - 1) if max_year else None
    prev_rows = [(y, m, a) for y, m, a in sale_rows if y == prev_year]
    prev_total = round(sum(a or 0 for y, m, a in prev_rows)) if prev_rows else None
    active_months = len({m for y, m, a in prev_rows if a})
    prev_avg = round(prev_total / active_months) if prev_total and active_months else None

    inv = InvoiceLine.objects.filter(customer_gc=group, is_cancelled=False)
    bill_tos = sorted({(b or '').strip() for b in inv.values_list('bill_to', flat=True)} - {''})
    assignees = sorted({(a or '').strip() for a in inv.values_list('assignee', flat=True)} - {''})

    context = {
        'group': group,
        'has_data': bool(customers or sale_rows or inv.exists()),
        'chart': chart,
        'prev_year': prev_year,
        'prev_total': prev_total,
        'prev_avg': prev_avg,
        'bill_tos': bill_tos,          # 費用請求先(請求台帳の会社名)
        'assignees': assignees,        # 外ロジ担当者(請求台帳の担当者)
    }
    return render(request, 'sales_trend/partner.html', context)


@login_required
def data_api(request):
    """四半期/月別 × (構成別系列 + 顧客別系列) を一括返却。切替は JS 側で即時。"""
    rows = list(MonthlySale.objects.values_list(
        'year', 'month', 'amount',
        'customer__klass', 'customer__other_start_year', 'customer__group_name',
    ))
    payload = build_payload(rows)
    payload['meta'] = [{'key': k, 'label': lbl, 'color': c} for k, lbl, c in SERIES]
    payload['by_customer'] = build_customer_payload(rows)
    return JsonResponse(payload)
