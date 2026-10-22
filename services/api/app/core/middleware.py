"""Cabeçalhos de segurança, id de requisição, log estruturado seguro e limite de taxa de autenticação."""

import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings

log = logging.getLogger("financemed.access")
_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
RATE_LIMITED = ("/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/change-password",
                "/api/v1/auth/mfa", "/api/v1/billing/webhook")  # fmt: skip


def client_ip(request: Request) -> str:
    """IP da conexão; só confia em X-Forwarded-For se `TRUST_PROXY_HEADERS=true` (proxy próprio)."""
    if get_settings().trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        if fwd:
            return fwd[:45]
    return request.client.host if request.client else "?"


def install(app: FastAPI) -> None:
    hits: dict[str, deque] = defaultdict(deque)
    app.state.rate_hits = hits  # por processo; com vários processos o limite é por instância

    @app.middleware("http")
    async def guard(request: Request, call_next):
        settings = (
            get_settings()
        )  # lido a cada requisição: configuração e testes têm o mesmo efeito
        t0 = time.perf_counter()
        rid = request.headers.get("x-request-id", "")
        rid = rid if _RID.match(rid) else uuid.uuid4().hex
        path = request.url.path
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > settings.max_body_bytes:
            return JSONResponse({"detail": "Corpo da requisição grande demais."}, status_code=413)
        if request.method == "POST" and path.startswith(RATE_LIMITED):
            ip = client_ip(request)
            q, now = hits[ip], time.monotonic()
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= settings.rate_limit_per_minute:
                resp = JSONResponse({"detail": "Muitas requisições. Aguarde um minuto."}, status_code=429,
                                    headers={"Retry-After": "60"})  # fmt: skip
            else:
                q.append(now)
                resp = await call_next(request)
        else:
            resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        if get_settings().cookie_secure:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Sem query string, corpo nem cabeçalhos: links de compartilhamento e dados não vão para o log.
        log.info(json.dumps({"rid": rid, "method": request.method, "path": path,
                             "status": resp.status_code, "ms": round((time.perf_counter() - t0) * 1000)}))  # fmt: skip
        return resp
