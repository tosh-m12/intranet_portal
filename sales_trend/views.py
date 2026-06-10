from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from .chartdata import SERIES, build_payload
from .models import MonthlySale


@login_required
def index(request):
    return render(request, 'sales_trend/index.html')


@login_required
def data_api(request):
    """四半期/月別 × 全系列の集計を一括返却。トグルは JS 側で即時切替。"""
    rows = MonthlySale.objects.values_list(
        'year', 'month', 'amount',
        'customer__klass', 'customer__other_start_year',
    )
    payload = build_payload(rows)
    payload['meta'] = [{'key': k, 'label': lbl, 'color': c} for k, lbl, c in SERIES]
    return JsonResponse(payload)
