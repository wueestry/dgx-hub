"""Streaming-safe httpx passthrough to a resolved backend address."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from starlette.requests import Request
from starlette.responses import StreamingResponse

_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
_EXCLUDED_REQUEST_HEADERS = {"host", "content-length"}
_EXCLUDED_RESPONSE_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "server",
    "date",
}


async def proxy_request(request: Request, backend_address: str, path: str) -> StreamingResponse:
    url = f"http://{backend_address}{path}"
    body = await request.body()

    client = httpx.AsyncClient(timeout=_TIMEOUT)
    upstream_request = client.build_request(
        request.method,
        url,
        headers=[
            (k, v) for k, v in request.headers.items() if k.lower() not in _EXCLUDED_REQUEST_HEADERS
        ],
        content=body,
    )
    upstream = await client.send(upstream_request, stream=True)

    async def body_iterator() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iterator(),
        status_code=upstream.status_code,
        headers={
            k: v for k, v in upstream.headers.items() if k.lower() not in _EXCLUDED_RESPONSE_HEADERS
        },
    )
