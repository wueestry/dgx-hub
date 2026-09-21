"""OpenAI-compatible reverse-proxy gateway."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from dgx_hub.gateway.proxy import proxy_request
from dgx_hub.gateway.registry import current_routes

app = FastAPI(title="dgx-hub gateway")


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    routes = current_routes()
    return JSONResponse(
        {"object": "list", "data": [{"id": model_id, "object": "model"} for model_id in routes]}
    )


@app.post("/v1/{path:path}")
async def proxy(path: str, request: Request) -> Response:
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    model_id = payload.get("model")
    routes = current_routes()

    if not model_id or model_id not in routes:
        return JSONResponse(
            {
                "error": {
                    "message": (
                        f"model {model_id!r} is not currently serving. "
                        f"Available: {sorted(routes)}"
                    )
                }
            },
            status_code=404,
        )

    return await proxy_request(request, routes[model_id], f"/v1/{path}")
