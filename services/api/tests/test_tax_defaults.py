"""Tributos PF/PJ: alíquotas médias padrão (27% PF, 15% PJ), habilitáveis e editáveis."""

import pytest

from tests.test_clinic import _setup
from tests.test_optimizer import run

M = "2026-10"


def work(api, regime="PJ", name="Plantão sem retenção", tax=None, gross=200_000):
    income = {
        "gross_cents": gross,
        "unit_type": "PER_EVENT",
        "duration_minutes": 720,
        "travel_minutes": 60,
    }
    if tax is not None:
        income["tax_rate_bps"] = tax
    r = api.req(
        "POST",
        "/api/v1/work-sources",
        json={"name": name, "work_type": "SHIFT", "regime": regime, "income": income},
    )
    assert r.status_code == 201, r.text
    return r.json()


def plan(api, kind, sid, units=4, tax="omit", month=None):
    body = {
        "month": month,
        "source_type": kind,
        "source_id": sid,
        "units": units,
        "certainty": "GUARANTEED",
    }
    if tax != "omit":
        body["tax_rate_bps"] = tax
    assert api.req("PUT", "/api/v1/income-plans", json=body).status_code == 200


def settings(api, enabled=True, pf=2_700, pj=1_500, clinic="PJ", expect=200, org_id=None):
    r = api.req(
        "PUT",
        "/api/v1/tax/settings",
        org_id=org_id,
        json={"enabled": enabled, "pf_bps": pf, "pj_bps": pj, "clinic_regime": clinic},
    )
    assert r.status_code == expect, r.text
    return r.json()


def rows(api, month=M):
    r = api.req("GET", f"/api/v1/tax/summary?month={month}")
    assert r.status_code == 200, r.text
    return {x["name"]: x for x in r.json()["rows"]}, r.json()


# ---------- configuração ----------


def test_defaults_are_27_pf_and_15_pj_and_disabled_until_enabled(make_user):
    api = make_user("a@x.com")
    s = api.req("GET", "/api/v1/tax/settings").json()
    assert (s["enabled"], s["pf_bps"], s["pj_bps"], s["clinic_regime"]) == (
        False,
        2_700,
        1_500,
        "PJ",
    )
    assert s["reference"] == {"pf_bps": 2_700, "pj_bps": 1_500} and any(
        "contador" in n for n in s["notes"]
    )


def test_owner_edits_and_enables(make_user):
    api = make_user("a@x.com")
    out = settings(api, enabled=True, pf=2_500, pj=1_200, clinic="PF")
    assert (out["enabled"], out["pf_bps"], out["pj_bps"], out["clinic_regime"]) == (
        True,
        2_500,
        1_200,
        "PF",
    )
    assert api.req("GET", "/api/v1/tax/settings").json()["pj_bps"] == 1_200
    assert settings(api, enabled=False)["enabled"] is False


@pytest.mark.parametrize(
    "pf,pj,clinic",
    [
        (10_001, 1_500, "PJ"),
        (-1, 1_500, "PJ"),
        (2_700, 10_001, "PJ"),
        (2_700, 1_500, "CLT"),
        (2_700, 1_500, ""),
    ],
)
def test_invalid_settings_rejected(make_user, pf, pj, clinic):
    api = make_user("a@x.com")
    settings(api, pf=pf, pj=pj, clinic=clinic, expect=422)


def test_only_the_owner_can_change_and_assistant_cannot_read(make_user):
    owner, doc, acc, sec = (make_user(f"{n}@x.com") for n in ("o", "d", "c", "s"))
    for u, role in ((doc, "PHYSICIAN"), (acc, "ACCOUNTANT"), (sec, "ASSISTANT")):
        owner.req(
            "POST",
            "/api/v1/team/members",
            json={"email": u.http.get("/api/v1/me").json()["email"], "role": role},
        )
    for u in (doc, acc):
        assert u.req("GET", "/api/v1/tax/settings", org_id=owner.org_id).status_code == 200
        settings(u, expect=403, org_id=owner.org_id)
    assert sec.req("GET", "/api/v1/tax/settings", org_id=owner.org_id).status_code == 403
    settings(owner)


def test_change_is_audited_and_isolated_between_organizations(make_user, app):
    from sqlalchemy import func, select

    from app.db.models import AuditLog

    a, b = make_user("a@x.com"), make_user("b@x.com")
    settings(a, pj=2_000)
    assert (
        b.req("GET", "/api/v1/tax/settings").json()["pj_bps"] == 1_500
        and b.req("GET", "/api/v1/tax/settings").json()["enabled"] is False
    )
    assert b.req("GET", "/api/v1/tax/settings", org_id=a.org_id).status_code == 404
    settings(b, org_id=a.org_id, expect=404)
    with app.state.session_factory() as s:
        assert (
            s.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "tax.settings")
            )
            == 1
        )


# ---------- efeito nos cálculos ----------


def test_disabled_means_no_default_tax(make_user):
    api = make_user("a@x.com")
    w = work(api)
    plan(api, "WORK", w["id"])
    r, _ = rows(api)
    x = r["Plantão sem retenção"]
    assert (x["tax_rate_bps"], x["tax_source"], x["tax_cents"]) == (0, "NENHUMA", 0)
    assert (
        w["income"]["tax_rate_bps"] is None and w["income"]["tax_rate_source"] == "NENHUMA"
    )  # guardada como "não informada"


def test_enabled_applies_pj_15_and_pf_27(make_user):
    api = make_user("a@x.com")
    settings(api)
    pj, pf = work(api, "PJ", "Renda PJ"), work(api, "PF", "Renda PF")
    plan(api, "WORK", pj["id"])
    plan(api, "WORK", pf["id"])
    r, tot = rows(api)
    assert (
        r["Renda PJ"]["tax_rate_bps"],
        r["Renda PJ"]["tax_source"],
        r["Renda PJ"]["tax_cents"],
    ) == (1_500, "PADRAO", 120_000)  # 15% de R$ 8.000
    assert (
        r["Renda PF"]["tax_rate_bps"],
        r["Renda PF"]["tax_source"],
        r["Renda PF"]["tax_cents"],
    ) == (2_700, "PADRAO", 216_000)  # 27%
    assert (
        r["Renda PJ"]["net_cents"] == 800_000 - 120_000
        and tot["uses_default"]
        and tot["total_tax_cents"] == 336_000
    )
    assert tot["tax_is_estimate"] is True


def test_edited_rates_are_used(make_user):
    api = make_user("a@x.com")
    settings(api, pf=2_000, pj=900)
    pj = work(api, "PJ", "Renda PJ")
    plan(api, "WORK", pj["id"])
    assert rows(api)[0]["Renda PJ"]["tax_cents"] == 72_000  # 9% de R$ 8.000


def test_clt_and_other_have_no_default(make_user):
    api = make_user("a@x.com")
    settings(api)
    clt, oth = work(api, "CLT", "Renda CLT"), work(api, "OTHER", "Outra renda")
    plan(api, "WORK", clt["id"])
    plan(api, "WORK", oth["id"])
    r, tot = rows(api)
    assert (
        all(
            r[n]["tax_source"] == "NENHUMA" and r[n]["tax_cents"] == 0
            for n in ("Renda CLT", "Outra renda")
        )
        and not tot["uses_default"]
    )


def test_informed_rate_always_beats_the_default_even_zero(make_user):
    api = make_user("a@x.com")
    settings(api)
    ten, zero = work(api, "PJ", "Com 10%", tax=1_000), work(api, "PJ", "Isento", tax=0)
    plan(api, "WORK", ten["id"])
    plan(api, "WORK", zero["id"])
    r, _ = rows(api)
    assert (r["Com 10%"]["tax_rate_bps"], r["Com 10%"]["tax_source"]) == (1_000, "INFORMADA")
    assert (r["Isento"]["tax_rate_bps"], r["Isento"]["tax_source"], r["Isento"]["tax_cents"]) == (
        0,
        "INFORMADA",
        0,
    )  # 0% informado não vira 15%


def test_monthly_plan_rate_beats_rule_and_default(make_user):
    api = make_user("a@x.com")
    settings(api)
    w = work(api, "PJ", "Renda PJ", tax=1_000)
    plan(api, "WORK", w["id"], tax=500)
    x = rows(api)[0]["Renda PJ"]
    assert (x["tax_rate_bps"], x["tax_source"], x["tax_cents"]) == (500, "INFORMADA", 40_000)
    plan(api, "WORK", w["id"], tax=None)  # plano sem taxa volta para a da renda
    assert rows(api)[0]["Renda PJ"]["tax_rate_bps"] == 1_000


def test_clinic_services_use_the_clinic_regime_default(make_user):
    api = make_user("a@x.com")
    _, svc = _setup(api)  # R$ 300 por consulta nova (retorno gratuito)
    plan(api, "SERVICE", svc["id"], units=10)
    assert rows(api)[0]["Consulta inicial"]["tax_source"] == "NENHUMA"
    settings(api, clinic="PJ")
    x = rows(api)[0]["Consulta inicial"]
    assert (x["tax_rate_bps"], x["tax_source"], x["tax_cents"]) == (
        1_500,
        "PADRAO",
        45_000,
    )  # 15% de R$ 3.000
    settings(api, clinic="PF")
    assert rows(api)[0]["Consulta inicial"]["tax_rate_bps"] == 2_700
    plan(api, "SERVICE", svc["id"], units=10, tax=1_000)
    assert rows(api)[0]["Consulta inicial"]["tax_source"] == "INFORMADA"


def test_work_listing_shows_effective_rate_and_net(make_user):
    api = make_user("a@x.com")
    w = work(api, "PF", "Renda PF")
    off = api.req("GET", f"/api/v1/work-sources/{w['id']}").json()["income"]
    assert off["effective_tax_bps"] == 0 and off["estimated_net_cents"] == 200_000
    settings(api)
    on = api.req("GET", f"/api/v1/work-sources/{w['id']}").json()["income"]
    assert (on["effective_tax_bps"], on["tax_rate_source"], on["tax_rate_bps"]) == (
        2_700,
        "PADRAO",
        None,
    )
    assert on["estimated_net_cents"] == 200_000 - 54_000
    assert api.req("GET", "/api/v1/work-sources").json()[0]["income"]["effective_tax_bps"] == 2_700


def test_dashboard_projection_follows_the_default(make_user):
    api = make_user("a@x.com")
    w = work(api, "PJ", "Renda PJ")
    plan(api, "WORK", w["id"])
    api.req(
        "POST",
        "/api/v1/goals",
        json={"month": M, "metric_type": "GROSS_REVENUE", "target_cents": 1_000_000},
    )
    api.req(
        "POST",
        "/api/v1/goals",
        json={"month": M, "metric_type": "PERSONAL_NET", "target_cents": 1_000_000},
    )
    before = api.req("GET", f"/api/v1/dashboard?month={M}").json()
    settings(api)
    after = api.req("GET", f"/api/v1/dashboard?month={M}").json()
    assert (
        before["projection"]["income_net_cents"] - after["projection"]["income_net_cents"]
        == 120_000
    )
    assert any("Nenhuma retenção" in r for r in before["data_quality_reasons"])
    assert not any(
        "Nenhuma retenção" in r for r in after["data_quality_reasons"]
    )  # agora há retenção estimada


def test_optimizer_uses_the_default_rate(make_user):
    api = make_user("a@x.com")
    w = work(api, "PJ", "Renda PJ")
    api.req(
        "POST",
        "/api/v1/availability/weekly",
        json={
            "weekdays": [0, 1, 2, 3, 4],
            "start_time": "08:00",
            "end_time": "22:00",
            "from_date": "2026-10-01",
            "to_date": "2026-10-31",
        },
    )
    acts = [
        {
            "source_type": "WORK",
            "source_id": w["id"],
            "max_units": 1,
            "candidate_days": ["2026-10-06"],
        }
    ]
    off = run(api, acts, target=5_000_000)
    settings(api)
    on = run(api, acts, target=5_000_000)
    assert off["max_achievable_gain_cents"] == 200_000
    assert on["max_achievable_gain_cents"] == 170_000  # 15% de retenção padrão


def test_assistant_uses_the_default_rate(make_user):
    api = make_user("a@x.com")
    w = work(api, "PJ", "Renda PJ")
    plan(api, "WORK", w["id"])

    def hourly():
        r = api.req(
            "POST",
            "/api/v1/assistant/chat",
            json={"message": "qual meu trabalho com maior renda por hora comprometida", "month": M},
        ).json()
        return next(f["value"] for f in r["message"]["answer"]["facts"] if f["key"] == "hour_0")

    off = hourly()
    settings(api)
    assert hourly() < off


def test_forecast_and_reports_follow_the_default(make_user):
    api = make_user("a@x.com")
    w = work(api, "PJ", "Renda PJ")
    plan(api, "WORK", w["id"])
    base = api.req(
        "POST", "/api/v1/forecasts/run", json={"as_of": "2026-10-15", "horizon_months": 3}
    ).json()
    settings(api)
    now = api.req(
        "POST", "/api/v1/forecasts/run", json={"as_of": "2026-10-15", "horizon_months": 3}
    ).json()
    assert now["months"][0]["plan_income_cents"] < base["months"][0]["plan_income_cents"]
