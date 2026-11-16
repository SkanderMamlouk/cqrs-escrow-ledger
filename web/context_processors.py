import time

from django.conf import settings

_cache: dict = {"t": 0.0, "provider": None}


def system_status(request):
    from ai_audit.providers import describe_provider
    from config.ledger import get_app
    from readmodel.projector import projection_status

    if time.time() - _cache["t"] > 30:
        _cache["provider"], _cache["t"] = describe_provider(), time.time()
    try:
        status = projection_status(get_app())
    except Exception:
        status = None
    return {"nav_status": status, "nav_provider": _cache["provider"], "nav_db_mode": settings.DB_MODE}

