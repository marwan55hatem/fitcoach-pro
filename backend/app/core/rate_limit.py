from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

# In-memory counters: per-process. Use a Redis storage_uri if the API ever runs multi-worker.
limiter = Limiter(key_func=get_remote_address)

# Evaluated per request so the limit can be changed through settings.
auth_limit = limiter.limit(lambda: get_settings().rate_limit_auth)
ai_limit = limiter.limit(lambda: get_settings().rate_limit_ai)
