"""Admission control: never let more requests run at once than the database pool can serve.

Sync endpoints keep one database session open across several thread hops, so when more
requests are in flight than there are pooled connections, threads block waiting for a
connection while the requests that hold connections wait for a thread: the whole server
wedges for tens of seconds. Capping in-flight requests at the pool size makes that
impossible, and anything beyond the cap gets a quick, clean 503 instead of a long hang.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

_BODY = json.dumps(
    {
        "detail": {
            "code": "server_busy",
            "message": "The server is busy right now. Please try again in a moment.",
        }
    }
).encode()


class ConcurrencyLimitMiddleware:
    """Pure ASGI middleware: at most `limit` HTTP requests in flight per worker process.

    A request waits up to `wait_s` for a free slot, then is rejected with 503 + Retry-After.
    Paths in `exempt` (health checks) bypass the limit so probes work while the app is busy.
    """

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        limit: int,
        wait_s: float = 1.0,
        exempt: tuple[str, ...] = ("/health",),
    ) -> None:
        self.app = app
        self.limit = limit
        self.wait_s = wait_s
        self.exempt = exempt
        self._semaphore: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _slots(self) -> asyncio.Semaphore:
        # One semaphore per event loop (the app runs on one loop; tests create several).
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._semaphore, self._loop = asyncio.Semaphore(self.limit), loop
        return self._semaphore

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt:
            await self.app(scope, receive, send)
            return
        slots = self._slots()
        try:
            await asyncio.wait_for(slots.acquire(), timeout=self.wait_s)
        except TimeoutError:
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", b"1"),
                        (b"content-length", str(len(_BODY)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": _BODY})
            return
        try:
            await self.app(scope, receive, send)
        finally:
            slots.release()
