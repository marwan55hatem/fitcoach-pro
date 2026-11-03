from slowapi import Limiter
from starlette.requests import Request

from app.core.config import Settings, get_settings


def client_ip(request: Request, settings: Settings) -> str:
    """The caller's address, looking through the web app's server when it forwards.

    X-Forwarded-For is only believed when the direct peer is a trusted proxy (the
    Next.js backend-for-frontend); the last hop that is not a trusted proxy wins, so a
    client cannot pick its own rate-limit bucket by sending the header itself.
    """
    peer = request.client.host if request.client else "unknown"
    trusted = set(settings.trusted_proxy_ips)
    if peer not in trusted:
        return peer
    hops = [
        h.strip()
        for h in request.headers.get("x-forwarded-for", "").split(",")
        if h.strip()
    ]
    for hop in reversed(hops):
        if hop not in trusted:
            return hop
    return peer


def _key(request: Request) -> str:
    return client_ip(request, get_settings())


# In-memory counters: per-process. Use a Redis storage_uri if the API ever runs multi-worker.
limiter = Limiter(key_func=_key)

# Evaluated per request so the limit can be changed through settings.
auth_limit = limiter.limit(lambda: get_settings().rate_limit_auth)
ai_limit = limiter.limit(lambda: get_settings().rate_limit_ai)
