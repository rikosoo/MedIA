"""Sprint 11: hardening — 2FA, bloqueio de login, sessões, exclusão/exportação (LGPD), retenção."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.security import decrypt_secret, totp_code, verify_totp
from app.db.models import (
    AiConversation,
    AuditLog,
    Invoice,
    Membership,
    Notification,
    Organization,
    Subscription,
    Transaction,
    User,
    UserSession,
    WorkSource,
)  # fmt: skip
from app.maintenance import purge
from tests.conftest import SHIFT
from tests.test_goals import goal

PWD = "senha-bem-longa-123"


def login(app, email="a@x.com", password=PWD, **kw):
    return TestClient(app).post(
        "/api/v1/auth/login", json={"email": email, "password": password, **kw}
    )


def secret_of(app, email):
    with app.state.session_factory() as s:
        return decrypt_secret(s.scalar(select(User).where(User.email == email)).mfa_secret_enc)


def enable_mfa(api, app, email="a@x.com"):
    r = api.req("POST", "/api/v1/auth/mfa/setup")
    assert r.status_code == 200 and r.json()["otpauth_uri"].startswith("otpauth://totp/")
    sec = secret_of(app, email)
    ok = api.req("POST", "/api/v1/auth/mfa/enable", json={"code": totp_code(sec)})
    assert ok.status_code == 200, ok.text
    return sec, ok.json()["recovery_codes"]


# ---------- cadastro, termos e senha ----------


def test_terms_must_be_accepted_and_are_recorded(app, make_user):
    c = TestClient(app)
    base = {"email": "n@x.com", "password": PWD, "name": "N", "organization_name": "O"}
    assert c.post("/api/v1/auth/register", json=base).status_code == 422
    assert c.post("/api/v1/auth/register", json={**base, "accept_terms": False}).status_code == 422
    assert c.post("/api/v1/auth/register", json={**base, "accept_terms": True}).status_code == 201
    with app.state.session_factory() as s:
        u = s.scalar(select(User).where(User.email == "n@x.com"))
        assert u.terms_accepted_at is not None and u.terms_version


@pytest.mark.parametrize("pwd", ["1234567890", "aaaaaaaaaaaa", "senha12345", "joaosilva-123456"])
def test_weak_passwords_rejected(app, pwd):
    r = TestClient(app).post("/api/v1/auth/register", json={"email": "joaosilva@x.com", "password": pwd,
        "name": "João", "organization_name": "O", "accept_terms": True})  # fmt: skip
    assert r.status_code == 422, pwd


def test_change_password_revokes_other_sessions(app, make_user):
    api = make_user("a@x.com")
    other = TestClient(app)
    other.post("/api/v1/auth/login", json={"email": "a@x.com", "password": PWD})
    assert other.get("/api/v1/me").status_code == 200
    bad = api.req(
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "errada-errada", "new_password": "outra-senha-forte-9"},
    )
    assert bad.status_code == 403
    assert (
        api.req(
            "POST",
            "/api/v1/auth/change-password",
            json={"current_password": PWD, "new_password": "outra-senha-forte-9"},
        ).status_code
        == 204
    )
    assert other.get("/api/v1/me").status_code == 401  # sessão antiga revogada
    assert api.http.get("/api/v1/me").status_code == 200  # a atual continua
    assert (
        login(app).status_code == 401
        and login(app, password="outra-senha-forte-9").status_code == 200
    )


def test_profile_update(make_user):
    api = make_user("a@x.com")
    assert api.req("PATCH", "/api/v1/me", json={"name": "  Dra. Nova  "}).json() == {
        "name": "Dra. Nova"
    }
    assert api.http.get("/api/v1/me").json()["name"] == "Dra. Nova"
    assert api.req("PATCH", "/api/v1/me", json={"name": ""}).status_code == 422


# ---------- bloqueio de força bruta ----------


def test_login_lockout_after_repeated_failures(app, make_user, monkeypatch):
    from app.core.config import get_settings

    make_user("a@x.com")
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "3")
    get_settings.cache_clear()
    try:
        for _ in range(3):
            assert login(app, password="errada-errada").status_code == 401
        r = login(app)  # senha certa, mas bloqueado
        assert r.status_code == 429 and "Tente novamente" in r.json()["detail"]
        with app.state.session_factory() as s:
            s.scalar(select(User)).locked_until = datetime.now(UTC) - timedelta(minutes=1)
            s.commit()
        assert login(app).status_code == 200
        with app.state.session_factory() as s:
            assert (
                s.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "auth.lockout")
                )
                == 1
            )
            assert s.scalar(select(User)).failed_logins == 0
    finally:
        get_settings.cache_clear()


def test_unknown_email_gets_same_answer_and_no_lock(app):
    for _ in range(8):
        assert login(app, "ninguem@x.com", "errada-errada").status_code == 401


def test_rate_limit_by_ip(app, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
    get_settings.cache_clear()
    try:
        from app.main import create_app

        c = TestClient(create_app())
        codes = [
            c.post(
                "/api/v1/auth/login", json={"email": "a@x.com", "password": "errada-errada"}
            ).status_code
            for _ in range(7)
        ]
        assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
        r = c.post("/api/v1/auth/login", json={"email": "a@x.com", "password": "x"})
        assert r.headers["retry-after"] == "60"
        assert c.get("/health").status_code == 200  # só a autenticação é limitada
    finally:
        get_settings.cache_clear()


# ---------- 2FA ----------


def test_totp_matches_rfc6238_vector():
    # Vetor do RFC 6238 (segredo "12345678901234567890", SHA-1), 6 dígitos.
    import base64

    sec = base64.b32encode(b"12345678901234567890").decode()
    assert totp_code(sec, 59) == "287082" and totp_code(sec, 1111111109) == "081804"
    assert verify_totp(sec, "287082", 59 + 25) and not verify_totp(sec, "287082", 59 + 120)


def test_mfa_full_flow_with_recovery_code(app, make_user):
    api = make_user("a@x.com")
    sec, codes = enable_mfa(api, app)
    assert len(codes) == 8
    with app.state.session_factory() as s:
        u = s.scalar(select(User))
        assert (
            u.mfa_enabled
            and sec not in (u.mfa_secret_enc or "")
            and all(c not in json.dumps(u.mfa_recovery_hashes) for c in codes)
        )
    assert login(app).json()["detail"] == "MFA_REQUIRED"
    assert login(app, otp="000000").status_code == 401
    assert login(app, otp=totp_code(sec)).status_code == 200
    assert login(app, otp=codes[0]).status_code == 200
    assert login(app, otp=codes[0]).status_code == 401  # código de recuperação vale uma vez
    assert api.req("POST", "/api/v1/auth/mfa/setup").status_code == 409


def test_mfa_enable_rejects_wrong_code_and_disable_needs_both(app, make_user):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/auth/mfa/setup")
    assert api.req("POST", "/api/v1/auth/mfa/enable", json={"code": "123456"}).status_code == 422
    sec, _ = enable_mfa(api, app)
    assert (
        api.req(
            "POST", "/api/v1/auth/mfa/disable", json={"password": "errada", "code": totp_code(sec)}
        ).status_code
        == 403
    )
    assert (
        api.req(
            "POST", "/api/v1/auth/mfa/disable", json={"password": PWD, "code": "000000"}
        ).status_code
        == 403
    )
    assert (
        api.req(
            "POST", "/api/v1/auth/mfa/disable", json={"password": PWD, "code": totp_code(sec)}
        ).status_code
        == 204
    )
    assert login(app).status_code == 200  # sem 2FA de novo


# ---------- sessões ----------


def test_sessions_list_and_revoke(app, make_user):
    api = make_user("a@x.com")
    other = TestClient(app)
    other.post("/api/v1/auth/login", json={"email": "a@x.com", "password": PWD})
    rows = api.req("GET", "/api/v1/auth/sessions").json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
    victim = next(r for r in rows if not r["current"])
    assert api.req("DELETE", f"/api/v1/auth/sessions/{victim['id']}").status_code == 204
    assert other.get("/api/v1/me").status_code == 401
    b = make_user("b@x.com")
    mine = api.req("GET", "/api/v1/auth/sessions").json()[0]["id"]
    assert (
        b.req("DELETE", f"/api/v1/auth/sessions/{mine}").status_code == 404
    )  # não revoga a de outro
    other.post("/api/v1/auth/login", json={"email": "a@x.com", "password": PWD})
    assert api.req("POST", "/api/v1/auth/sessions/revoke-others").status_code == 204
    assert len(api.req("GET", "/api/v1/auth/sessions").json()) == 1


# ---------- cabeçalhos, id de requisição, /ready ----------


def test_security_headers_and_request_id(app):
    c = TestClient(app)
    r = c.get("/health", headers={"X-Request-ID": "abc-12345678"})
    assert r.headers["x-request-id"] == "abc-12345678"
    assert (
        r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    )
    assert c.get("/health", headers={"X-Request-ID": "x\ny"}).headers["x-request-id"] != "x\ny"
    assert c.get("/api/v1/me").headers["cache-control"] == "no-store"


def test_access_log_has_no_query_string(app, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="financemed.access"):
        TestClient(app).get("/health?token=SEGREDO123")
    assert caplog.records and all("SEGREDO123" not in r.getMessage() for r in caplog.records)
    assert json.loads(caplog.records[-1].getMessage())["path"] == "/health"


def test_ready_checks_the_database(app):
    assert TestClient(app).get("/ready").json() == {"status": "ready"}


def test_production_requires_real_secret(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "prod")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            get_settings()
    finally:
        get_settings.cache_clear()


# ---------- exportação pessoal ----------


def test_personal_export(make_user):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/work-sources", json=SHIFT)
    r = api.req("GET", "/api/v1/me/export")
    d = r.json()
    assert (
        d["profile"]["email"] == "a@x.com"
        and len(d["work_sources"]) == 1
        and d["memberships"][0]["role"] == "OWNER"
    )
    assert "password" not in r.text and "mfa_secret" not in r.text


# ---------- exclusão de conta ----------


def delete_me(api, **kw):
    body = {"password": PWD, "email": api.http.get("/api/v1/me").json()["email"], **kw}
    return api.req("DELETE", "/api/v1/me", json=body)


def test_delete_account_purges_org_but_keeps_invoices(app, make_user):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/work-sources", json=SHIFT)
    goal(api, 3_000_000, month="2026-10")
    api.req(
        "POST",
        "/api/v1/assistant/chat",
        json={"message": "explique os números", "month": "2026-10"},
    )
    api.req("PUT", "/api/v1/billing/payment-method", json={"token": "tok_visa"})
    api.req("PATCH", "/api/v1/billing/subscription", json={"plan": "PRO"})
    org = api.org_id
    r = delete_me(api)
    assert r.status_code == 204
    assert api.http.get("/api/v1/me").status_code == 401
    assert login(app).status_code == 401
    with app.state.session_factory() as s:
        for model in (WorkSource, Transaction, AiConversation, Membership):
            assert s.scalar(select(func.count()).select_from(model)) == 0, model
        u = s.scalar(select(User))
        assert (
            u.deleted_at
            and u.email.endswith("@invalid.local")
            and u.name == "Usuário removido"
            and not u.mfa_enabled
        )
        assert s.get(Organization, __import__("uuid").UUID(org)).name == "Organização removida"
        assert s.scalar(select(func.count()).select_from(Invoice)) == 1  # retenção fiscal
        sub = s.scalar(select(Subscription))
        assert sub.plan == "FREE" and sub.payment_last4 is None and sub.payment_token is None
        assert (
            s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "account.deleted")
            )
            == 1
        )
        assert not any(
            str(a.metadata_json).find("a@x.com") >= 0 for a in s.scalars(select(AuditLog))
        )
    # o e-mail pode ser reaproveitado para um novo cadastro
    assert (
        TestClient(app)
        .post(
            "/api/v1/auth/register",
            json={
                "email": "a@x.com",
                "password": PWD,
                "name": "Nova",
                "organization_name": "O",
                "accept_terms": True,
            },
        )
        .status_code
        == 201
    )


def test_delete_requires_password_email_and_otp(app, make_user):
    api = make_user("a@x.com")
    assert (
        api.req("DELETE", "/api/v1/me", json={"password": PWD, "email": "outro@x.com"}).status_code
        == 422
    )
    assert (
        api.req(
            "DELETE", "/api/v1/me", json={"password": "errada-errada", "email": "a@x.com"}
        ).status_code
        == 403
    )
    sec, _ = enable_mfa(api, app)
    assert delete_me(api).status_code == 403  # falta o código do 2FA
    assert delete_me(api, otp=totp_code(sec)).status_code == 204


def test_owner_with_team_must_remove_members_first(app, make_user):
    owner, _doc = make_user("o@x.com"), make_user("d@x.com")
    owner.req("POST", "/api/v1/team/members", json={"email": "d@x.com", "role": "ASSISTANT"})
    assert delete_me(owner).status_code == 409
    assert owner.http.get("/api/v1/me").status_code == 200


def test_member_leaving_keeps_the_business_but_loses_personal_data(app, make_user):
    from app.db.models import MonthlyGoal

    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    owner.req("POST", "/api/v1/team/members", json={"email": "d@x.com", "role": "PHYSICIAN"})
    doc.req("POST", "/api/v1/work-sources", org_id=owner.org_id, json=SHIFT)
    doc.req(
        "POST",
        "/api/v1/goals",
        org_id=owner.org_id,
        json={"month": "2026-10", "metric_type": "PERSONAL_NET", "target_cents": 1_000_000},
    )
    owner.req(
        "POST",
        "/api/v1/transactions",
        json={
            "type": "INCOME",
            "status": "REALIZED",
            "competence_month": "2026-10",
            "paid_at": "2026-10-05",
            "amount_cents": 5000,
            "category": "consultas",
            "description": "Receita do consultório",
        },
    )
    assert delete_me(doc).status_code == 204
    with app.state.session_factory() as s:
        assert (
            s.scalar(select(func.count()).select_from(Transaction)) == 1
        )  # negócio da clínica fica
        assert s.scalar(select(func.count()).select_from(MonthlyGoal)) == 0  # meta pessoal some
        assert (
            s.scalar(select(WorkSource)).deleted_at is not None
        )  # vínculo pessoal sai de circulação
        assert s.scalar(select(func.count()).select_from(Membership)) == 1  # só o dono
    assert owner.http.get("/api/v1/me").status_code == 200


# ---------- retenção ----------


def test_retention_purge(app, make_user):
    api = make_user("a@x.com")
    now = datetime.now(UTC)
    with app.state.session_factory() as s:
        uid = s.scalar(select(User)).id
        org = s.scalar(select(Organization)).id
        s.add(
            Notification(
                organization_id=org,
                user_id=uid,
                kind="GOAL_GAP",
                ref="2026-10",
                title="t",
                body="b",
                route="/",
                read_at=now - timedelta(days=200),
            )
        )
        s.add(
            Notification(
                organization_id=org,
                user_id=uid,
                kind="GOAL_GAP",
                ref="2026-11",
                title="t",
                body="b",
                route="/",
                read_at=now - timedelta(days=10),
            )
        )
        s.add(
            AiConversation(
                organization_id=org,
                owner_user_id=uid,
                summary="velha",
                deleted_at=now - timedelta(days=40),
            )
        )
        s.add(
            AiConversation(
                organization_id=org,
                owner_user_id=uid,
                summary="recente",
                deleted_at=now - timedelta(days=2),
            )
        )
        s.add(
            UserSession(
                user_id=uid,
                token_hash="x" * 64,
                csrf_token="c",
                created_at=now - timedelta(days=3),
                expires_at=now - timedelta(days=1),
            )
        )
        s.commit()
        dry = purge(s, now, dry_run=True)
        assert dry["sessions"] == 1 and dry["notifications"] == 1 and dry["ai_conversations"] == 1
        assert s.scalar(select(func.count()).select_from(UserSession)) == 2  # dry-run não apaga
        out = purge(s, now)
        assert out == dry
        assert s.scalar(select(func.count()).select_from(Notification)) == 1
        assert [c.summary for c in s.scalars(select(AiConversation))] == ["recente"]
        assert s.scalar(select(func.count()).select_from(UserSession)) == 1  # só a ativa
    assert api.http.get("/api/v1/me").status_code == 200


# ---------- primeiros passos ----------


def test_onboarding_checklist_follows_real_data(make_user):
    api = make_user("a@x.com")
    ob = api.req("GET", "/api/v1/onboarding").json()
    assert ob["total"] == 7 and ob["done"] == 0 and not ob["complete"]
    api.req("POST", "/api/v1/work-sources", json=SHIFT)
    goal(api, 3_000_000, month="2026-10")
    ob = api.req("GET", "/api/v1/onboarding").json()
    done = {s["key"] for s in ob["steps"] if s["done"]}
    assert done == {"work", "goal"} and ob["done"] == 2


def test_onboarding_hides_steps_the_role_cannot_do(make_user):
    owner, sec = make_user("o@x.com"), make_user("s@x.com")
    owner.req("POST", "/api/v1/team/members", json={"email": "s@x.com", "role": "ASSISTANT"})
    keys = {
        s["key"] for s in sec.req("GET", "/api/v1/onboarding", org_id=owner.org_id).json()["steps"]
    }
    assert "goal" not in keys and "work" not in keys and "availability" in keys
