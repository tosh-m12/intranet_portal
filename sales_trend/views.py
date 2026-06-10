from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from .chartdata import SERIES, build_customer_payload, build_payload
from .models import MonthlySale


@login_required
def index(request):
    return render(request, 'sales_trend/index.html')


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
