import json

from django import template

from banking.money import fmt

register = template.Library()


@register.filter
def tnd(millimes):
    try:
        return fmt(int(millimes or 0), with_currency=False)
    except (TypeError, ValueError):
        return millimes


@register.filter
def tojson(value):
    def default(o):
        return o.isoformat() if hasattr(o, "isoformat") else str(o)

    return json.dumps(value, indent=2, default=default, ensure_ascii=False)


@register.filter
def short(value):
    return str(value)[:8]


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None

