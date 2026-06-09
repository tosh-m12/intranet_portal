"""積み上げグラフの系列定義と集計ロジック。

系列(stacking)の並びは引き継ぎ §7-2 に準拠:
  上→下: Faurecia / 大口 / その他22以前 / 23 / 24 / 25 / 26 / グレー(最下段)
色は意味で固定: 青系=既存の大口アンカー、緑系=「その他」コホート(新しいほど淡), グレー=調整。
ラベルは日中両言語対応のため gettext。集計時は active 言語で評価される。
"""
from django.utils.translation import gettext_lazy as _

# (key, label, color)  ※視覚上の上→下の順。Chart.js では JS 側で下→上に積む。
SERIES = [
    ('faurecia',   _('Faurecia'),                '#1f3864'),
    ('major',      _('大口顧客'),                 '#3a6ea5'),
    ('other_2022', _('その他22以前'),             '#2e6e4f'),
    ('other_2023', _('その他23新規'),             '#4e9e6f'),
    ('other_2024', _('その他24新規'),             '#7dbf8a'),
    ('other_2025', _('その他25新規'),             '#aed5a0'),
    ('other_2026', _('その他26新規'),             '#d9e8b0'),
    ('grey',       _('グレー(調整・予估・外包)'),   '#c2c6cd'),
]
SERIES_KEYS = [s[0] for s in SERIES]


def series_key(klass, other_start_year):
    """顧客の klass / other_start_year を系列キーに変換。"""
    if klass == 'other':
        return f'other_{other_start_year or 2022}'
    return klass


def _qtr_label(year, month):
    return f'{year}Q{(month - 1) // 3 + 1}'


def _month_label(year, month):
    return f'{year}/{month:02d}'


def build_payload(rows):
    """rows: (year, month, amount, klass, other_start_year) の iterable。

    戻り値: {'qtr': {...}, 'month': {...}}。各 period 種別ごとに
    {'labels': [...], 'series': {key: [値...]}} を持つ。値は系列キー順に整列。
    """
    out = {}
    for period, labeler in (('qtr', _qtr_label), ('month', _month_label)):
        buckets = {}          # label -> {series_key: amount}
        for year, month, amount, klass, osy in rows:
            label = labeler(year, month)
            key = series_key(klass, osy)
            slot = buckets.setdefault(label, {})
            slot[key] = slot.get(key, 0.0) + (amount or 0.0)
        labels = sorted(buckets.keys())
        series = {
            k: [round(buckets[lab].get(k, 0.0)) for lab in labels]
            for k in SERIES_KEYS
        }
        out[period] = {'labels': labels, 'series': series}
    return out
