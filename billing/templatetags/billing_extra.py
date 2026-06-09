"""請求入力フォーム用テンプレートフィルタ。"""
from django import template

register = template.Library()


def _fmt(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


@register.filter
def fee_val(obj, key):
    """費目の税抜額(obj が None=新規なら空)。"""
    if obj is None:
        return ''
    return _fmt(getattr(obj, key, None))


@register.filter
def fee_rate_val(obj, key):
    """費目の税率%。"""
    if obj is None:
        return ''
    return _fmt(getattr(obj, f'{key}_rate', None))
