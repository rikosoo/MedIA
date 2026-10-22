"""Varredura de todas as rotas: autenticação obrigatória e isolamento entre organizações (US-010)."""

import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.deps import get_auth, get_tenant

PUBLIC = {
    ("GET", "/health"), ("GET", "/ready"),
    ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/billing/webhook"),  # autenticado por assinatura HMAC
    ("GET", "/api/v1/shared-reports/{token}"),  # link temporário: o token de 256 bits é a credencial
}  # fmt: skip


def _calls(dep) -> set:
    out = {dep.call}
    for d in dep.dependencies:
        out |= _calls(d)
    return out


def all_routes(app):
    """Rotas da API, inclusive as dos routers incluídos (o FastAPI atual as mantém aninhadas)."""
    for r in app.routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from (x for x in r.original_router.routes if isinstance(x, APIRoute))


def routes(app, dependency):
    for r in all_routes(app):
        for method in r.methods - {"HEAD", "OPTIONS"}:
            if dependency in _calls(r.dependant):
                yield method, r.path


def fill(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", str(uuid.UUID(int=0)), path)


def test_every_route_is_either_public_or_requires_a_session(app):
    anon = TestClient(app)
    seen = set()
    for r in all_routes(app):
        for method in r.methods - {"HEAD", "OPTIONS"}:
            seen.add((method, r.path))
    assert len(seen) > 140, len(seen)  # a varredura enxerga a API inteira, não uma lista vazia
    # rotas públicas conhecidas: precisam estar na lista; as demais precisam exigir sessão
    protected = set(routes(app, get_auth)) | set(routes(app, get_tenant))
    unprotected = {(m, p) for m, p in seen if (m, p) not in protected and (m, p) not in PUBLIC}
    assert not unprotected, f"rotas sem autenticação fora da lista pública: {sorted(unprotected)}"
    for method, path in sorted(protected):
        r = anon.request(method, fill(path), json={} if method != "GET" else None)
        assert r.status_code == 401, (method, path, r.status_code)


def test_tenant_routes_hide_other_organizations(app, make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    tenant_routes = sorted(set(routes(app, get_tenant)))
    assert len(tenant_routes) > 100  # a varredura cobre a API inteira
    leaks = []
    for method, path in tenant_routes:
        r = b.req(method, fill(path), org_id=a.org_id, json={} if method != "GET" else None)
        if r.status_code != 404 or r.json().get("detail") != "Organização não encontrada":
            leaks.append((method, path, r.status_code))
    assert not leaks, leaks


def test_tenant_routes_require_the_organization_header(app, make_user):
    a = make_user("a@x.com")
    sample = sorted(set(routes(app, get_tenant)))[:40]
    for method, path in sample:
        r = a.http.request(
            method,
            fill(path),
            headers={"X-CSRF-Token": a.csrf},
            json={} if method != "GET" else None,
        )
        assert r.status_code == 400, (method, path, r.status_code)


@pytest.mark.parametrize(
    "method,path",
    [("POST", "/api/v1/work-sources"), ("PATCH", "/api/v1/me"), ("DELETE", "/api/v1/me")],
)
def test_mutations_without_csrf_are_rejected(make_user, method, path):
    a = make_user("a@x.com")
    r = a.http.request(method, path, headers={"X-Organization-Id": a.org_id}, json={"x": 1})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
