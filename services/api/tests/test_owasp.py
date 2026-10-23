"""Regressões de segurança por categoria do OWASP Top 10 (2021): injeção, XSS, acesso, configuração."""

import io
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import User, WorkSource
from tests.conftest import SHIFT
from tests.test_goals import goal

SQLI = [
    "' OR '1'='1", "'; DROP TABLE users; --", '" OR ""="', "1; SELECT pg_sleep(5)--",
    "%' UNION SELECT password_hash FROM users --", "\\'; DELETE FROM transactions; --",
    "Robert'); DROP TABLE work_sources;--",
]  # fmt: skip
XSS = [
    "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", '"><svg/onload=alert(1)>',
    "javascript:alert(1)", "<b>negrito</i></font>", "{{7*7}}", "${7*7}",
]  # fmt: skip
M = "2026-10"


def users_intact(app, expected=1):
    with app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(User)) >= expected


# ---------- A03: injeção (SQL) ----------


@pytest.mark.parametrize("payload", SQLI)
def test_sql_injection_payloads_are_stored_as_plain_text(app, make_user, payload):
    api = make_user("a@x.com")
    other = make_user("b@x.com")
    r = api.req(
        "POST",
        "/api/v1/work-sources",
        json={**SHIFT, "name": payload, "payer_name": payload, "notes": payload},
    )
    assert r.status_code == 201, r.text
    assert (
        api.req("GET", "/api/v1/work-sources").json()[0]["name"] == payload
    )  # literal, sem efeito
    tx = api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED", "competence_month": M,
        "amount_cents": 100, "category": "outros", "description": payload})  # fmt: skip
    assert tx.status_code == 201, tx.text
    ch = api.req(
        "POST", "/api/v1/acquisition/channels", json={"name": payload, "channel_type": "GOOGLE"}
    )
    assert ch.status_code in (201, 422)
    assert api.req(
        "POST", "/api/v1/assistant/chat", json={"message": payload + " ganhar"}
    ).status_code in (200, 402)
    assert users_intact(app, 2) and other.http.get("/api/v1/me").status_code == 200
    assert (
        len(other.req("GET", "/api/v1/work-sources").json()) == 0
    )  # nada vazou para outro cliente


@pytest.mark.parametrize("payload", SQLI)
def test_sql_injection_in_query_and_path_never_reaches_a_500(app, make_user, payload):
    api = make_user("a@x.com")
    from urllib.parse import quote

    q = quote(payload, safe="")
    urls = [f"/api/v1/transactions?month={q}", f"/api/v1/transactions?type={q}", f"/api/v1/dashboard?month={q}",
            f"/api/v1/export/transactions.csv?month={q}", f"/api/v1/work-sources/{q}", f"/api/v1/goals/{q}/gap",
            f"/api/v1/alerts?month={q}", f"/api/v1/forecasts/{q}", f"/api/v1/assistant/conversations/{q}"]  # fmt: skip
    for u in urls:
        r = api.req("GET", u)
        assert r.status_code in (404, 422, 400), (u, r.status_code)
        if "/export/" in u or "month=" in u:
            assert r.status_code == 422, (u, r.status_code)  # o parâmetro é validado, não ignorado
    assert users_intact(app)


def test_sql_injection_in_login_and_register_fields(app):
    c = TestClient(app)
    for p in SQLI:
        r = c.post("/api/v1/auth/login", json={"email": "a@x.com", "password": p})
        assert r.status_code == 401
        assert c.post(
            "/api/v1/auth/login", json={"email": f"{p}@x.com", "password": "x"}
        ).status_code in (401, 422)
        assert c.post("/api/v1/auth/register", json={"email": "z@x.com", "password": "Senha-bem-longa-123!", "name": p,
            "organization_name": p, "accept_terms": True}).status_code in (201, 409)  # fmt: skip
    assert users_intact(app)


def test_csv_import_with_injection_payloads(app, make_user):
    api = make_user("a@x.com")
    csv = "competencia,tipo,situacao,categoria,descricao,valor\n" + "\n".join(
        f'2026-10,EXPENSE,PLANNED,outros,"{p.replace(chr(34), chr(39))}",10,00' for p in SQLI
    )
    r = api.req(
        "POST",
        "/api/v1/imports",
        json={"filename": "../../etc/passwd;rm -rf.csv", "content": csv, "dry_run": True},
    )
    assert r.status_code == 200, r.text
    assert users_intact(app)


def test_orm_only_no_string_built_sql_in_app_code():
    """Guarda de código: nenhum SQL montado por concatenação/f-string fora do `SELECT 1` do /ready."""
    bad = []
    for f in Path(__file__).parents[1].joinpath("app").rglob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if (
                re.search(
                    r"(execute|text)\(\s*f?[\"'].*(SELECT|INSERT|UPDATE|DELETE)\b", line, re.I
                )
                and "SELECT 1" not in line
            ):
                bad.append(f"{f.name}:{n}")
            if re.search(r"f[\"'](SELECT|INSERT|UPDATE|DELETE)\b", line, re.I):
                bad.append(f"{f.name}:{n}")
    assert not bad, bad


# ---------- A03: XSS / injeção de conteúdo ----------


@pytest.mark.parametrize("payload", XSS)
def test_api_returns_user_text_as_json_never_html(make_user, payload):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/work-sources", json={**SHIFT, "name": payload})
    r = api.req("GET", "/api/v1/work-sources")
    assert r.headers["content-type"].startswith("application/json")
    assert (
        r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    )
    assert r.json()[0]["name"] == payload  # guardado literal; quem exibe (React) escapa


@pytest.mark.parametrize("payload", XSS)
def test_pdf_and_csv_reports_survive_markup_in_user_text(make_user, payload):
    api = make_user("a@x.com")
    w = api.req("POST", "/api/v1/work-sources", json={**SHIFT, "name": payload}).json()
    api.req(
        "PUT",
        "/api/v1/income-plans",
        json={
            "month": None,
            "source_type": "WORK",
            "source_id": w["id"],
            "units": 2,
            "certainty": "GUARANTEED",
        },
    )
    goal(api, 1_000_000, month=M)
    pdf = api.req(
        "POST",
        "/api/v1/reports/generate",
        json={"template": "MONTHLY_RESULT", "period": M, "format": "PDF"},
    )
    assert pdf.status_code == 201, pdf.text
    rid = pdf.json()["id"]
    raw = api.req("GET", f"/api/v1/reports/{rid}/download")
    assert raw.status_code == 200 and raw.headers["content-type"] == "application/pdf"
    assert (
        raw.headers["content-disposition"].startswith("attachment")
        and raw.headers["x-content-type-options"] == "nosniff"
    )
    text = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(raw.content)).pages)
    flat = re.sub(r"\s+", "", text)
    assert (
        re.sub(r"\s+", "", payload) in flat
    )  # a marcação aparece como texto literal, não vira estilo
    csv = api.req(
        "POST",
        "/api/v1/reports/generate",
        json={"template": "MONTHLY_RESULT", "period": M, "format": "CSV"},
    )
    assert csv.status_code == 201


@pytest.mark.parametrize("cell", ["=cmd|' /C calc'!A0", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"])
def test_csv_exports_neutralize_formula_injection(make_user, cell):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED", "competence_month": M,
        "amount_cents": 100, "category": "outros", "description": cell})  # fmt: skip
    r = api.req("GET", f"/api/v1/export/transactions.csv?month={M}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    body = r.content.decode("utf-8-sig")
    row = body.splitlines()[1] if len(body.splitlines()) > 1 else ""
    assert not re.search(r"(^|,)\"?[=+\-@\t\r]", row.split(",outros,", 1)[-1]), row


def test_header_injection_through_filenames_is_blocked(make_user):
    api = make_user("a@x.com")
    for period in ("2026-10\r\nX-Evil: 1", 'a"b\r\n1', "2026-13", "20261"):
        r = api.req(
            "POST",
            "/api/v1/reports/generate",
            json={"template": "MONTHLY_RESULT", "period": period, "format": "PDF"},
        )
        assert r.status_code == 422, period
    assert (
        api.req("GET", "/api/v1/export/transactions.csv?month=2026-10%0d%0aX-Evil:1").status_code
        == 422
    )
    from app.modules.reports.router import safe_filename

    assert safe_filename('a"b\r\n/../c.pdf') == "a_b____.._c.pdf".replace(
        "..", ".."
    ) or "\n" not in safe_filename('a"b\r\n/../c.pdf')
    assert re.fullmatch(r"[A-Za-z0-9._-]+", safe_filename('x"\r\n;/\\y.pdf'))


# ---------- A01: controle de acesso / atribuição em massa ----------


def test_mass_assignment_fields_are_ignored(app, make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    with app.state.session_factory() as s:
        b_id = s.scalar(select(User).where(User.email == "b@x.com")).id
    r = a.req(
        "POST",
        "/api/v1/work-sources",
        json={
            **SHIFT,
            "organization_id": b.org_id,
            "owner_user_id": str(b_id),
            "created_by": str(b_id),
            "id": "00000000-0000-0000-0000-000000000001",
            "deleted_at": "2020-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 201
    with app.state.session_factory() as s:
        w = s.scalar(select(WorkSource))
        assert (
            str(w.organization_id) == a.org_id and w.owner_user_id != b_id and w.deleted_at is None
        )
        assert str(w.id) != "00000000-0000-0000-0000-000000000001"
    me = a.req(
        "PATCH",
        "/api/v1/me",
        json={
            "name": "Nova",
            "email": "evil@x.com",
            "password_hash": "x",
            "mfa_enabled": True,
            "deleted_at": "2020-01-01",
        },
    )
    assert me.status_code == 200 and a.http.get("/api/v1/me").json()["email"] == "a@x.com"
    with app.state.session_factory() as s:
        u = s.scalar(select(User).where(User.email == "a@x.com"))
        assert not u.mfa_enabled and u.deleted_at is None and u.password_hash != "x"


def test_register_cannot_choose_a_role_or_join_an_existing_org(app, make_user):
    owner = make_user("o@x.com")
    c = TestClient(app)
    r = c.post("/api/v1/auth/register", json={"email": "n@x.com", "password": "Senha-bem-longa-123!", "name": "N", "organization_name": "O",
        "accept_terms": True, "role": "OWNER", "organization_id": owner.org_id, "is_admin": True})  # fmt: skip
    assert r.status_code == 201
    orgs = c.get("/api/v1/me").json()["organizations"]
    assert len(orgs) == 1 and orgs[0]["id"] != owner.org_id


def test_non_owner_cannot_escalate(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    owner.req("POST", "/api/v1/team/members", json={"email": "d@x.com", "role": "PHYSICIAN"})
    assert (
        doc.req(
            "PATCH", "/api/v1/organizations/current", org_id=owner.org_id, json={"name": "Invadida"}
        ).status_code
        == 403
    )
    assert (
        doc.req(
            "POST",
            "/api/v1/team/members",
            org_id=owner.org_id,
            json={"email": "d@x.com", "role": "OWNER"},
        ).status_code
        == 403
    )
    assert doc.req("GET", "/api/v1/billing/subscription", org_id=owner.org_id).status_code == 403


def test_personal_objects_are_not_readable_by_org_peers(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    owner.req("POST", "/api/v1/team/members", json={"email": "d@x.com", "role": "PHYSICIAN"})
    g = goal(owner, 3_000_000, month=M)
    assert doc.req("GET", f"/api/v1/goals/{g['id']}/gap", org_id=owner.org_id).status_code == 404
    assert (
        doc.req(
            "PATCH", f"/api/v1/goals/{g['id']}", org_id=owner.org_id, json={"target_cents": 1}
        ).status_code
        == 404
    )
    assert doc.req("DELETE", f"/api/v1/goals/{g['id']}", org_id=owner.org_id).status_code == 404


# ---------- A02/A07: sessão e dados sensíveis ----------


def test_cookie_flags_and_no_secrets_in_responses(app, make_user):
    api = make_user("a@x.com")
    login = TestClient(app).post(
        "/api/v1/auth/login", json={"email": "a@x.com", "password": "Senha-bem-longa-123!"}
    )
    ck = login.headers["set-cookie"].lower()
    assert "httponly" in ck and "samesite=lax" in ck and "path=/" in ck
    assert "fm_session" not in login.text and "password" not in login.text
    for path in (
        "/api/v1/me",
        "/api/v1/me/export",
        "/api/v1/auth/sessions",
        "/api/v1/billing/subscription",
    ):
        body = api.req("GET", path).text.lower()
        assert (
            "password_hash" not in body
            and "$argon2" not in body
            and "mfa_secret" not in body
            and "token_hash" not in body
        ), path


def test_secure_cookie_flag_when_configured(app, make_user, monkeypatch):
    make_user("a@x.com")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    get_settings.cache_clear()
    try:
        r = TestClient(app).post(
            "/api/v1/auth/login", json={"email": "a@x.com", "password": "Senha-bem-longa-123!"}
        )
        assert (
            "secure" in r.headers["set-cookie"].lower() and "strict-transport-security" in r.headers
        )
    finally:
        get_settings.cache_clear()


def test_session_is_rotated_on_login_and_dead_after_logout(app, make_user):
    api = make_user("a@x.com")
    old = api.http.cookies.get("fm_session")
    c = TestClient(app)
    c.post("/api/v1/auth/login", json={"email": "a@x.com", "password": "Senha-bem-longa-123!"})
    assert c.cookies.get("fm_session") != old  # novo token a cada login (sem fixação de sessão)
    stolen = TestClient(app)
    stolen.cookies.set("fm_session", old)
    api.req("POST", "/api/v1/auth/logout")
    assert stolen.get("/api/v1/me").status_code == 401
    forged = TestClient(app)
    forged.cookies.set("fm_session", "x" * 43)
    assert forged.get("/api/v1/me").status_code == 401


def test_login_failures_are_audited_without_the_attempted_password(app, make_user):
    from app.db.models import AuditLog

    make_user("a@x.com")
    TestClient(app).post(
        "/api/v1/auth/login", json={"email": "a@x.com", "password": "SENHA-ERRADA-SECRETA"}
    )
    with app.state.session_factory() as s:
        rows = list(s.scalars(select(AuditLog).where(AuditLog.action == "auth.login_failed")))
        assert len(rows) == 1 and "SENHA-ERRADA" not in str(rows[0].metadata_json)


# ---------- A05: configuração ----------


def test_cors_only_allows_configured_origin(app):
    c = TestClient(app)
    ok = c.get("/health", headers={"Origin": "http://localhost:3000"})
    assert (
        ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
        and ok.headers.get("access-control-allow-credentials") == "true"
    )
    evil = c.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    pre = c.options(
        "/api/v1/me",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in pre.headers


def test_wildcard_cors_is_refused(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_api_docs_are_off_outside_dev(monkeypatch):
    from app.main import create_app

    c = TestClient(create_app())
    assert c.get("/docs").status_code == 200  # dev
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    get_settings.cache_clear()
    try:
        p = TestClient(create_app())
        assert all(p.get(u).status_code == 404 for u in ("/docs", "/redoc", "/openapi.json"))
    finally:
        get_settings.cache_clear()


def test_oversized_body_is_rejected(app, make_user):
    api = make_user("a@x.com")
    r = api.http.post(
        "/api/v1/work-sources", headers={**api.h(), "Content-Length": "999999999"}, content=b"{}"
    )
    assert r.status_code == 413
    big = api.req("POST", "/api/v1/imports", json={"filename": "a.csv", "content": "x" * 2_000_001})
    assert big.status_code == 422


def test_unhandled_errors_do_not_leak_internals(app):
    app.get("/__boom")(lambda: 1 / 0)
    r = TestClient(app, raise_server_exceptions=False).get("/__boom")
    assert (
        r.status_code == 500
        and "Traceback" not in r.text
        and "ZeroDivision" not in r.text
        and "/home/" not in r.text
    )


def test_forwarded_for_is_ignored_unless_proxy_is_trusted(app, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    try:
        c = TestClient(app)
        codes = [
            c.post(
                "/api/v1/auth/login",
                json={"email": "a@x.com", "password": "x"},
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
            ).status_code
            for i in range(5)
        ]
        assert codes[3:] == [429, 429]  # trocar o cabeçalho não burla o limite
    finally:
        get_settings.cache_clear()


# ---------- A10: SSRF / A08: integridade ----------


def test_backend_makes_no_outbound_http_requests():
    """Sem cliente HTTP no código da aplicação não há superfície de SSRF; ao adotar um provedor
    externo (pagamento/LLM) este teste deve ser revisto junto com uma lista de destinos permitidos."""
    offenders = []
    for f in Path(__file__).parents[1].joinpath("app").rglob("*.py"):
        if re.search(
            r"^\s*(import|from)\s+(httpx|requests|urllib\.request|aiohttp|socket|http\.client)\b",
            f.read_text(),
            re.M,
        ):
            offenders.append(f.name)
    assert not offenders, offenders


def test_webhook_rejects_tampered_payload(app, make_user, monkeypatch):
    import hashlib
    import hmac
    import json

    api = make_user("a@x.com")
    api.req("GET", "/api/v1/billing/subscription")
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "s3cret")
    get_settings.cache_clear()
    try:
        body = json.dumps(
            {"event_id": "e1", "type": "subscription.deleted", "organization_id": api.org_id}
        ).encode()
        sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        tampered = body.replace(b"subscription.deleted", b"invoice.payment_succeeded")
        c = TestClient(app)
        assert (
            c.post(
                "/api/v1/billing/webhook", content=tampered, headers={"X-Signature": sig}
            ).status_code
            == 401
        )
        assert (
            c.post(
                "/api/v1/billing/webhook", content=body, headers={"X-Signature": sig}
            ).status_code
            == 200
        )
    finally:
        get_settings.cache_clear()
