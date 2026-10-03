"""Cache policy: no shared cache (Cloudflare, a proxy) may keep an answer of this API.

Every response leaves with a ``Cache-Control`` header. The static handler sets its own (the HTML
shell is revalidated, content-hashed ``/assets`` are immutable, both ``private``); everything
else - API reads, the error envelopes, the event stream - is ``no-store``, because it is live,
per-principal state served behind Cloudflare Access.
"""

from __future__ import annotations

__all__ = ["ASSET", "NO_STORE", "SHELL", "NoStoreByDefault"]

NO_STORE = "no-store"
# the HTML shell and unhashed files: a browser may keep them but must revalidate each time
SHELL = "private, no-cache"
# Vite's content-hashed build output: the name changes whenever the bytes do
ASSET = "private, max-age=31536000, immutable"

_HEADER = b"cache-control"


class NoStoreByDefault:
    """ASGI middleware: add ``Cache-Control: no-store`` to any HTTP response that has none."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_policy(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", ()))
                if not any(name.lower() == _HEADER for name, _ in headers):
                    headers.append((_HEADER, NO_STORE.encode()))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_policy)
