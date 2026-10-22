"""Sprint 10: assinaturas, limites por plano, cancelamento/upgrade, falha de pagamento e exportação."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import AuditLog, BillingEvent, Subscription
from tests.conftest import SHIFT
from tests.test_goals import goal
from tests.test_optimizer import acts, run, setup

M = "2026-10"
T0 = datetime(2030, 1, 10, 12, 0, tzinfo=UTC)  # relógio fixo bem depois do fim do teste grátis


class Clock:
    def __init__(self, monkeypatch):
        self.now = None
        for mod in ("app.modules.billing.entitlements", "app.modules.billing.router"):
            monkeypatch.setattr(f"{mod}.utcnow", lambda: self.now or datetime.now(UTC))

    def at(self, dt):
        self.now = dt


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


def sub(api):
    r = api.req("GET", "/api/v1/billing/subscription")
    assert r.status_code == 200, r.text
    return r.json()


def pay(api, token="tok_visa", expect=200):
    r = api.req("PUT", "/api/v1/billing/payment-method", json={"token": token})
    assert r.status_code == expect, r.text
    return r.json()


def change(api, plan, expect=200, **h):
    r = api.http.request("PATCH", "/api/v1/billing/subscription", json={"plan": plan},
                         headers={**api.h(), **h})  # fmt: skip
    assert r.status_code == expect, r.text
    return r.json()


def invoices(api):
    return api.req("GET", "/api/v1/billing/invoices").json()


def free(api, clock):
    """Fim do teste grátis: a organização cai para o plano Gratuito."""
    clock.at(T0)
    assert sub(api)["status"] == "FREE"


def webhook(app, payload, secret="s3cret", sign=True, raw=None):
    from fastapi.testclient import TestClient

    body = raw if raw is not None else json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return TestClient(app).post("/api/v1/billing/webhook", content=body,
                                headers={"X-Signature": sig if sign else "sha256=bad"})  # fmt: skip


# ---------- catálogo e teste grátis ----------


def test_catalog_prices(make_user):
    api = make_user("a@x.com")
    r = api.req("GET", "/api/v1/billing/plans").json()
    assert [(p["plan"], p["price_cents"]) for p in r] == [
        ("FREE", 0), ("PRO", 3900), ("PRO_AI", 7900), ("CLINIC", 19900)]  # fmt: skip


def test_new_org_gets_a_14_day_trial_with_everything(make_user):
    api = make_user("a@x.com")
    s = sub(api)
    assert s["status"] == "TRIALING" and s["in_trial"] and s["plan"] == "FREE"
    assert s["effective_plan"] == "CLINIC" and set(s["features"]) >= {
        "reports",
        "forecasts",
        "assistant",
        "team",
    }
    ends = datetime.fromisoformat(s["trial_ends_at"])
    assert 13 <= (ends - datetime.now(UTC)).days <= 14
    assert "simulada" in s["provider_note"]


# ---------- limites do plano Gratuito (dados existentes nunca somem) ----------


def test_free_plan_limits(make_user, clock):
    api = make_user("a@x.com")
    shift = api.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    api.req("POST", "/api/v1/work-sources", json={**SHIFT, "name": "Outro plantão"})
    goal(api, 3_000_000, month=M)
    free(api, clock)
    r = api.req("POST", "/api/v1/work-sources", json={**SHIFT, "name": "Terceiro"})
    assert (
        r.status_code == 402
        and "Gratuito" in r.json()["detail"]
        and "2 trabalhos" in r.json()["detail"]
    )
    assert (
        api.req(
            "POST",
            "/api/v1/goals",
            json={"month": M, "metric_type": "GROSS_REVENUE", "target_cents": 1},
        ).status_code
        == 402
    )
    assert goal(api, 1_000_000, month="2026-11")["month"] == "2026-11"  # outro mês tem a sua cota
    for path in ("/api/v1/forecasts/run", "/api/v1/assistant/chat", "/api/v1/reports/generate"):
        body = (
            {"horizon_months": 3}
            if "forecasts" in path
            else (
                {"message": "explique os números"}
                if "assistant" in path
                else {"template": "MONTHLY_RESULT", "period": M, "format": "PDF"}
            )
        )
        r = api.req("POST", path, json=body)
        assert r.status_code == 402, path
    assert (
        api.req(
            "POST", "/api/v1/reports/preview", json={"template": "MONTHLY_RESULT", "period": M}
        ).status_code
        == 402
    )
    # dados existentes continuam legíveis
    assert len(api.req("GET", "/api/v1/work-sources").json()) == 2
    assert api.req("GET", f"/api/v1/work-sources/{shift['id']}").status_code == 200


def test_free_optimizer_allows_two_runs_per_month(make_user, app):
    api = make_user("a@x.com")
    s = setup(api)
    with (
        app.state.session_factory() as db
    ):  # fim do teste grátis, sem mexer no relógio das simulações
        row = db.scalar(select(Subscription))
        if row is None:
            sub(api)
            row = db.scalar(select(Subscription))
        row.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    assert sub(api)["status"] == "FREE"
    for _ in range(2):
        run(api, acts(*s))
    r = run(api, acts(*s), expect=402)
    assert "simulações" in r["detail"] and "Gratuito" in r["detail"]


def test_team_requires_clinic_and_caps_professionals(make_user, clock):
    owner = make_user("o@x.com")
    others = [make_user(f"d{i}@x.com") for i in range(5)]
    clock.at(T0)
    emails = lambda u: u.http.get("/api/v1/me").json()["email"]  # noqa: E731
    r = owner.req(
        "POST", "/api/v1/team/members", json={"email": emails(others[0]), "role": "PHYSICIAN"}
    )
    assert r.status_code == 402 and "Clínica" in r.json()["detail"]
    pay(owner)
    change(owner, "CLINIC")
    for u in others[:4]:
        assert (
            owner.req(
                "POST", "/api/v1/team/members", json={"email": emails(u), "role": "PHYSICIAN"}
            ).status_code
            == 201
        )
    r = owner.req(
        "POST", "/api/v1/team/members", json={"email": emails(others[4]), "role": "PHYSICIAN"}
    )
    assert r.status_code == 402 and "5 profissionais" in r.json()["detail"]
    assert (
        owner.req(
            "POST", "/api/v1/team/members", json={"email": emails(others[4]), "role": "ASSISTANT"}
        ).status_code
        == 201
    )


# ---------- pagamento e upgrade ----------


def test_payment_method_never_takes_card_numbers(make_user, app):
    api = make_user("a@x.com")
    for bad in ("4242 4242 4242 4242", "4242424242424242"):
        assert (
            api.req("PUT", "/api/v1/billing/payment-method", json={"token": bad}).status_code == 422
        )
    assert (
        api.req(
            "PUT", "/api/v1/billing/payment-method", json={"token": "tok_inexistente"}
        ).status_code
        == 422
    )
    assert pay(api)["payment_method"] == {"brand": "Visa", "last4": "4242"}
    with app.state.session_factory() as s:
        assert s.scalar(select(Subscription)).payment_last4 == "4242"


def test_start_paid_plan_charges_and_ends_trial(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    assert change(api, "PRO", expect=409)["detail"].startswith("Cadastre")
    pay(api)
    out = change(api, "PRO")
    assert out["status"] == "ACTIVE" and out["plan"] == "PRO" and not out["in_trial"]
    assert out["change"]["charge_today_cents"] == 3900
    inv = invoices(api)
    assert (
        len(inv) == 1
        and inv[0]["status"] == "PAID"
        and inv[0]["amount_cents"] == 3900
        and inv[0]["number"] == "FM-000001"
    )
    assert out["next_billing_at"].startswith("2030-02-09")
    # agora relatórios funcionam; previsões ainda não
    assert (
        api.req(
            "POST", "/api/v1/reports/preview", json={"template": "MONTHLY_RESULT", "period": M}
        ).status_code
        == 200
    )
    assert api.req("POST", "/api/v1/forecasts/run", json={"horizon_months": 3}).status_code == 402


def test_declined_card_changes_nothing(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api, "tok_fail")
    r = change(api, "PRO", expect=402)
    assert "recusado" in r["detail"]
    s = sub(api)
    assert s["plan"] == "FREE" and s["status"] == "FREE"
    assert [(i["status"], i["amount_cents"]) for i in invoices(api)] == [("FAILED", 3900)]


def test_upgrade_is_prorated_and_immediate(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api)
    change(api, "PRO")
    clock.at(T0 + timedelta(days=15))
    prev = api.req("POST", "/api/v1/billing/subscription/preview", json={"plan": "PRO_AI"}).json()
    assert (
        prev["action"] == "UPGRADE_NOW" and prev["charge_today_cents"] == (7900 - 3900) * 15 // 30
    )
    out = change(api, "PRO_AI")
    assert out["plan"] == "PRO_AI" and out["current_period_end"].startswith("2030-02-09")
    assert invoices(api)[0]["amount_cents"] == 2000
    assert api.req("POST", "/api/v1/forecasts/run", json={"horizon_months": 3}).status_code == 200


def test_downgrade_waits_for_period_end_then_renews_cheaper(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api)
    change(api, "PRO_AI")
    out = change(api, "PRO")
    assert (
        out["plan"] == "PRO_AI"
        and out["pending_plan"] == "PRO"
        and out["change"]["action"] == "DOWNGRADE_AT_PERIOD_END"
    )
    assert (
        api.req("POST", "/api/v1/forecasts/run", json={"horizon_months": 3}).status_code == 200
    )  # ainda vale
    clock.at(T0 + timedelta(days=31))
    s = sub(api)
    assert s["plan"] == "PRO" and s["pending_plan"] is None and s["status"] == "ACTIVE"
    assert invoices(api)[0]["amount_cents"] == 3900
    assert api.req("POST", "/api/v1/forecasts/run", json={"horizon_months": 3}).status_code == 402


def test_same_plan_and_invalid_plan(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    assert change(api, "FREE", expect=409)
    assert (
        api.req("PATCH", "/api/v1/billing/subscription", json={"plan": "ENTERPRISE"}).status_code
        == 422
    )


def test_idempotent_change_does_not_charge_twice(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api)
    change(api, "PRO", **{"Idempotency-Key": "abc-1"})
    again = change(api, "PRO", **{"Idempotency-Key": "abc-1"})
    assert again["replayed"] is True
    assert len(invoices(api)) == 1


# ---------- cancelamento ----------


def test_cancel_keeps_access_until_period_end(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api)
    change(api, "PRO")
    assert api.req("POST", "/api/v1/billing/cancel", json={"confirm": False}).status_code == 422
    r = api.req("POST", "/api/v1/billing/cancel", json={"confirm": True}).json()
    assert r["status"] == "CANCELING" and r["cancel_at_period_end"] and r["next_billing_at"] is None
    assert "não há reembolso" in r["message"] and "exportação" in r["message"]
    assert api.req("POST", "/api/v1/billing/cancel", json={"confirm": True}).status_code == 409
    clock.at(T0 + timedelta(days=10))
    assert (
        api.req(
            "POST", "/api/v1/reports/preview", json={"template": "MONTHLY_RESULT", "period": M}
        ).status_code
        == 200
    )
    clock.at(T0 + timedelta(days=31))
    s = sub(api)
    assert (
        s["plan"] == "FREE" and s["status"] == "FREE" and len(invoices(api)) == 1
    )  # sem nova cobrança
    assert (
        api.req(
            "POST", "/api/v1/reports/preview", json={"template": "MONTHLY_RESULT", "period": M}
        ).status_code
        == 402
    )
    assert api.req("POST", "/api/v1/billing/cancel", json={"confirm": True}).status_code == 409


def test_resume_undoes_scheduled_cancel(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api)
    change(api, "PRO")
    api.req("POST", "/api/v1/billing/cancel", json={"confirm": True})
    assert api.req("POST", "/api/v1/billing/resume").json()["status"] == "ACTIVE"
    assert api.req("POST", "/api/v1/billing/resume").status_code == 409
    clock.at(T0 + timedelta(days=31))
    assert sub(api)["plan"] == "PRO" and len(invoices(api)) == 2  # renovou


def test_downgrade_to_free_is_a_scheduled_cancel(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api)
    change(api, "PRO")
    assert change(api, "FREE")["status"] == "CANCELING"


def test_cancel_on_free_plan_conflicts(make_user, clock):
    api = make_user("a@x.com")
    free(api, clock)
    assert api.req("POST", "/api/v1/billing/cancel", json={"confirm": True}).status_code == 409


# ---------- falha de pagamento ----------


def test_failed_renewal_grace_alert_and_recovery(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api, "tok_fail_renewal")
    change(api, "PRO")
    api.req(
        "PUT",
        "/api/v1/notification-settings",
        json={"enabled": True, "kinds": ["SUBSCRIPTION_AT_RISK"]},
    )
    clock.at(T0 + timedelta(days=31))
    s = sub(api)
    assert s["status"] == "PAST_DUE" and s["plan"] == "PRO" and s["grace_ends_at"]
    assert invoices(api)[0]["status"] == "FAILED"
    assert (
        api.req(
            "POST", "/api/v1/reports/preview", json={"template": "MONTHLY_RESULT", "period": M}
        ).status_code
        == 200
    )  # carência
    alerts = api.req("GET", f"/api/v1/alerts?month={M}").json()
    assert any(
        a["kind"] == "SUBSCRIPTION_AT_RISK"
        for a in (alerts["alerts"] if isinstance(alerts, dict) else alerts)
    )
    assert change(api, "PRO_AI", expect=409)
    out = pay(api, "tok_visa")  # nova forma de pagamento: cobra de novo
    assert out["retried_charge"] is True and out["status"] == "ACTIVE"
    assert invoices(api)[0]["status"] == "PAID"


def test_grace_expires_back_to_free(make_user, clock):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api, "tok_fail_renewal")
    change(api, "PRO")
    clock.at(T0 + timedelta(days=31))
    assert sub(api)["status"] == "PAST_DUE"
    clock.at(T0 + timedelta(days=39))
    s = sub(api)
    assert s["status"] == "FREE" and s["plan"] == "FREE"
    assert (
        api.req(
            "POST", "/api/v1/reports/preview", json={"template": "MONTHLY_RESULT", "period": M}
        ).status_code
        == 402
    )


# ---------- webhook ----------


def test_webhook_signature_idempotency_and_states(make_user, app, monkeypatch):
    api = make_user("a@x.com")
    pay(api)
    org = api.org_id
    assert webhook(app, {}, secret="x").status_code == 503  # sem segredo configurado
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "s3cret")
    get_settings.cache_clear()
    try:
        ev = {"event_id": "evt_1", "type": "invoice.payment_failed", "organization_id": org}
        assert webhook(app, ev, sign=False).status_code == 401
        assert webhook(app, ev, secret="outro").status_code == 401
        assert webhook(app, None, raw=b"nao-json").status_code == 422
        # plano gratuito não entra em atraso por evento
        assert webhook(app, ev).json()["status"] == "processed" and sub(api)["status"] != "PAST_DUE"
        sub_ = sub(api)  # ainda em teste
        assert sub_["plan"] == "FREE"
        with app.state.session_factory() as s:
            row = s.scalar(select(Subscription))
            row.plan = "PRO"
            s.commit()
        ev2 = {**ev, "event_id": "evt_2"}
        assert webhook(app, ev2).json()["status"] == "processed"
        assert sub(api)["status"] == "PAST_DUE"
        assert webhook(app, ev2).json()["status"] == "duplicate"
        ok = {"event_id": "evt_3", "type": "invoice.payment_succeeded", "organization_id": org}
        webhook(app, ok)
        assert sub(api)["status"] == "ACTIVE"
        webhook(app, {"event_id": "evt_4", "type": "subscription.deleted", "organization_id": org})
        assert sub(api)["plan"] == "FREE"
        webhook(app, {"event_id": "evt_5", "type": "coisa.desconhecida", "organization_id": org})
        assert (
            webhook(
                app,
                {
                    "event_id": "evt_6",
                    "type": "x",
                    "organization_id": "00000000-0000-0000-0000-000000000000",
                },
            ).status_code
            == 404
        )
        with app.state.session_factory() as s:
            assert s.scalar(select(func.count()).select_from(BillingEvent)) == 5
    finally:
        get_settings.cache_clear()


# ---------- permissões, isolamento e exportação ----------


def test_only_the_owner_manages_billing(make_user):
    owner, doc, sec = make_user("o@x.com"), make_user("d@x.com"), make_user("s@x.com")
    for u, role in ((doc, "PHYSICIAN"), (sec, "ASSISTANT")):
        owner.req(
            "POST",
            "/api/v1/team/members",
            json={"email": u.http.get("/api/v1/me").json()["email"], "role": role},
        )
    for u in (doc, sec):
        for method, path, body in (
            ("GET", "/billing/subscription", None),
            ("GET", "/billing/plans", None),
            ("GET", "/billing/invoices", None),
            ("GET", "/billing/export", None),
            ("PUT", "/billing/payment-method", {"token": "tok_visa"}),
            ("PATCH", "/billing/subscription", {"plan": "PRO"}),
            ("POST", "/billing/cancel", {"confirm": True}),
        ):
            r = u.req(
                method, "/api/v1" + path, org_id=owner.org_id, **({"json": body} if body else {})
            )
            assert r.status_code == 403, (path, r.status_code)


def test_billing_is_tenant_isolated(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    pay(a)
    assert b.req("GET", "/api/v1/billing/subscription", org_id=a.org_id).status_code == 404
    assert sub(b)["payment_method"] is None and invoices(b) == []
    assert a.http.get("/api/v1/billing/subscription").status_code in (
        400,
        401,
        403,
    )  # sem header de organização


def test_export_works_on_any_plan_and_is_audited(make_user, clock, app):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/work-sources", json=SHIFT)
    goal(api, 3_000_000, month=M)
    free(api, clock)
    r = api.req("GET", "/api/v1/billing/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert (
        len(data["work_sources"]) == 1
        and data["work_sources"][0]["income_rule"]["gross_cents"] == 200_000
    )
    assert data["goals"][0]["target_cents"] == 3_000_000 and data["organization"]["name"]
    assert "password" not in r.text and "token" not in json.dumps(data["subscription"]).replace(
        "provider_note", ""
    )
    with app.state.session_factory() as s:
        assert (
            s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "billing.export")
            )
            == 1
        )


def test_export_excludes_other_users_personal_data(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    owner.req(
        "POST",
        "/api/v1/team/members",
        json={"email": doc.http.get("/api/v1/me").json()["email"], "role": "PHYSICIAN"},
    )
    doc.req("POST", "/api/v1/work-sources", org_id=owner.org_id, json=SHIFT)
    assert owner.req("GET", "/api/v1/billing/export").json()["work_sources"] == []


def test_audit_log_has_no_amounts(make_user, clock, app):
    api = make_user("a@x.com")
    clock.at(T0)
    pay(api)
    change(api, "PRO")
    with app.state.session_factory() as s:
        for lg in s.scalars(select(AuditLog).where(AuditLog.action.like("billing.%"))):
            assert all(k in {"from", "to", "plan"} for k in lg.metadata_json)


def test_cors_preflight_allows_the_idempotency_key(app):
    from fastapi.testclient import TestClient

    r = TestClient(app).options("/api/v1/billing/subscription", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "idempotency-key,x-csrf-token,content-type"})  # fmt: skip
    assert (
        r.status_code == 200
        and "idempotency-key" in r.headers["access-control-allow-headers"].lower()
    )
