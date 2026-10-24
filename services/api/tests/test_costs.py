"""Custo fixo por hora e ponto de equilíbrio do consultório."""

import pytest

from tests.test_clinic import _setup
from tests.test_goals import tx

M = "2026-10"


def rec(api, category, amount, frequency="MONTHLY", start="2026-01-01", **kw):
    r = api.req("POST", "/api/v1/recurrences", json={"description": f"Custo {category}", "category": category,
        "amount_cents": amount, "frequency": frequency, "start_date": start, **kw})  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def plan(api, svc, units):
    r = api.req("PUT", "/api/v1/income-plans", json={"month": None, "source_type": "SERVICE", "source_id": svc["id"],
        "units": units, "certainty": "PROBABLE"})  # fmt: skip
    assert r.status_code == 200, r.text


def report(api, month=M, expect=200, **q):
    qs = "&".join([f"month={month}"] + [f"{k}={v}" for k, v in q.items()])
    r = api.req("GET", f"/api/v1/costs/breakeven?{qs}")
    assert r.status_code == expect, r.text
    return r.json()


def scenario(api, units=20):
    _, svc = _setup(api)  # R$ 300, 55,2 min por consulta nova, retorno gratuito
    rec(api, "aluguel", 300_000)
    rec(api, "secretaria", 200_000)
    rec(api, "equipamentos", 1_200_000, "YEARLY")  # R$ 12.000/ano = R$ 1.000/mês
    plan(api, svc, units)
    return svc


def test_fixed_cost_sums_default_categories_and_amortizes_yearly(make_user):
    api = make_user("a@x.com")
    scenario(api)
    tx(
        api, category="material", amount_cents=50_000, description="Material"
    )  # variável: fora do fixo
    r = report(api)
    assert r["fixed"]["total_cents"] == 300_000 + 200_000 + 100_000
    by = {c["category"]: c for c in r["fixed"]["by_category"]}
    assert by["equipamentos"]["amortized_cents"] == 100_000 and by["equipamentos"]["included"]
    assert by["aluguel"]["projected_cents"] == 300_000  # recorrência ainda sem lançamento
    assert by["material"]["included"] is False and by["material"]["realized_cents"] == 50_000


def test_per_hour_and_per_consult_numbers(make_user):
    api = make_user("a@x.com")
    scenario(api, units=20)
    r = report(api)
    # 20 consultas x 55,2 min = 1.104 min = 18,4 h -> R$ 6.000 / 18,4 h = R$ 326,09/h
    assert r["hours"]["planned_minutes"] == 1_104 and r["hours"]["basis"] == "PLANNED"
    assert r["hours"]["fixed_per_hour_planned_cents"] == 32_609
    s = r["services"][0]
    assert s["margin_per_consult_cents"] == 30_000 and s["planned_units"] == 20
    assert (
        s["fixed_per_consult_cents"] == 30_000
    )  # o rateio por tempo devolve o fixo inteiro: 6.000/20
    assert s["net_per_consult_cents"] == 0 and s["margin_per_hour_cents"] == 32_609
    assert s["net_per_hour_cents"] == 0


def test_breakeven_and_status(make_user):
    api = make_user("a@x.com")
    scenario(api, units=12)  # contribuição planejada R$ 3.600 < R$ 6.000 de fixo
    be = report(api)["breakeven"]
    assert be["status"] == "BELOW" and be["total_units"] == 20  # 6.000 / 300
    assert be["result_cents"] == 12 * 30_000 - 600_000 and be["safety_bps"] < 0
    assert be["minutes"] == 1_104
    r = report(api)
    assert (
        r["services"][0]["breakeven_alone_units"] == 20
        and r["services"][0]["breakeven_units"] == 20
    )


def test_above_breakeven_has_safety_margin(make_user):
    api = make_user("a@x.com")
    scenario(api, units=40)
    be = report(api)["breakeven"]
    assert (
        be["status"] == "ABOVE" and be["result_cents"] == 600_000 and be["safety_bps"] == 5_000
    )  # (40-20)/40


def test_available_hours_override_and_fit(make_user):
    api = make_user("a@x.com")
    scenario(api, units=12)
    r = report(api, available_minutes=1_000)  # 16h40: o equilíbrio (1.104 min) não cabe
    assert (
        r["hours"]["available_source"] == "MANUAL"
        and r["hours"]["fixed_per_hour_available_cents"] == 36_000
    )
    assert r["breakeven"]["fits_schedule"] is False and r["breakeven"]["occupancy_bps"] == 11_040
    assert (
        r["hours"]["idle_cost_cents"] == 36_000 * (1_000 - 662) // 60
    )  # 338 min ociosos a R$ 360/h
    ok = report(api, available_minutes=6_000)
    assert (
        ok["breakeven"]["fits_schedule"] is True
        and ok["hours"]["idle_cost_cents"] == 6_000 * (6_000 - 662) // 60
    )
    assert report(api, available_minutes=6_000, basis="AVAILABLE")["hours"]["basis"] == "AVAILABLE"


def test_capacity_from_the_agenda(make_user):
    api = make_user("a@x.com")
    scenario(api)
    api.req("POST", "/api/v1/availability/weekly", json={"weekdays": [0, 1, 2, 3, 4], "start_time": "08:00",
        "end_time": "12:00", "from_date": "2026-10-01", "to_date": "2026-10-31"})  # fmt: skip
    r = report(api)
    assert (
        r["hours"]["available_source"] == "AGENDA" and r["hours"]["available_minutes"] == 22 * 240
    )


def test_without_agenda_fit_is_unknown_not_false(make_user):
    api = make_user("a@x.com")
    scenario(api, units=12)
    be = report(api)["breakeven"]
    assert be["total_units"] == 20 and be["fits_schedule"] is None and be["occupancy_bps"] is None


def test_category_selection_and_manual_fixed_cost(make_user):
    api = make_user("a@x.com")
    scenario(api)
    only = report(api, categories="aluguel")
    assert only["fixed"]["total_cents"] == 300_000
    none = report(api, categories="")
    assert none["fixed"]["total_cents"] == 0 and any("custo fixo" in w for w in none["warnings"])
    manual = report(api, fixed_cents=1_000_000)
    assert (
        manual["fixed"]["total_cents"] == 1_000_000
        and manual["fixed"]["manual"]
        and manual["fixed"]["computed_cents"] == 600_000
    )
    report(api, categories="inexistente", expect=422)
    report(api, month="2026-13", expect=422)
    report(api, fixed_cents=-1, expect=422)


def test_realized_and_planned_expenses_count_without_duplication(make_user):
    api = make_user("a@x.com")
    _setup(api)
    tx(api, category="aluguel", amount_cents=300_000, description="Aluguel pago")
    tx(
        api,
        status="PLANNED",
        paid_at=None,
        category="secretaria",
        amount_cents=200_000,
        description="Salário previsto",
    )
    r = report(api)
    assert r["fixed"]["total_cents"] == 500_000
    by = {c["category"]: c for c in r["fixed"]["by_category"]}
    assert (
        by["aluguel"]["realized_cents"] == 300_000 and by["secretaria"]["planned_cents"] == 200_000
    )


def test_materialized_recurrence_is_not_counted_twice(make_user):
    api = make_user("a@x.com")
    _setup(api)
    r = rec(api, "aluguel", 300_000)
    assert report(api)["fixed"]["total_cents"] == 300_000
    api.req("POST", f"/api/v1/recurrences/{r['id']}/materialize?month={M}")
    assert report(api)["fixed"]["total_cents"] == 300_000


def test_yearly_cost_outside_validity_does_not_count(make_user):
    api = make_user("a@x.com")
    _setup(api)
    rec(api, "equipamentos", 1_200_000, "YEARLY", start="2026-11-01")  # começa depois do mês
    rec(api, "software", 600_000, "YEARLY", start="2025-01-01", stop_date="2026-06-30")  # já acabou
    assert report(api)["fixed"]["total_cents"] == 0


def test_warnings_for_missing_data(make_user):
    api = make_user("a@x.com")
    r = report(api)
    msgs = " ".join(r["warnings"])
    assert (
        "Nenhum serviço" in msgs
        and "Nenhum custo fixo" in msgs
        and r["breakeven"]["status"] == "NO_PLAN"
    )
    _setup(api)
    assert "Nenhuma consulta planejada" in " ".join(report(api)["warnings"])


def test_service_with_negative_margin_is_flagged(make_user):
    api = make_user("a@x.com")
    _, svc = _setup(api, service={"cost_cents": 40_000})  # custo por consulta > preço de R$ 300
    rec(api, "aluguel", 100_000)
    plan(api, svc, 10)
    r = report(api)
    assert (
        r["services"][0]["margin_per_consult_cents"] < 0
        and r["breakeven"]["status"] == "UNREACHABLE"
    )
    assert r["services"][0]["breakeven_alone_units"] is None and "margem" in " ".join(r["warnings"])


def test_read_only_and_permissions(make_user, app):
    from sqlalchemy import func, select

    from app.db.models import Transaction

    owner, doc, sec, acc = (make_user(f"{n}@x.com") for n in ("o", "d", "s", "c"))
    for u, role in ((doc, "PHYSICIAN"), (sec, "ASSISTANT"), (acc, "ACCOUNTANT")):
        owner.req(
            "POST",
            "/api/v1/team/members",
            json={"email": u.http.get("/api/v1/me").json()["email"], "role": role},
        )
    scenario(owner)
    with app.state.session_factory() as s:
        before = s.scalar(select(func.count()).select_from(Transaction))
    assert report(owner)["fixed"]["total_cents"] == 600_000
    with app.state.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == before  # não gravou nada
    assert (
        doc.req("GET", f"/api/v1/costs/breakeven?month={M}", org_id=owner.org_id).status_code == 200
    )
    assert (
        sec.req("GET", f"/api/v1/costs/breakeven?month={M}", org_id=owner.org_id).status_code == 403
    )  # sem finanças
    assert (
        acc.req("GET", f"/api/v1/costs/breakeven?month={M}", org_id=owner.org_id).status_code == 200
    )  # contador lê finanças e clínica


def test_tenant_isolation(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    scenario(a)
    assert b.req("GET", f"/api/v1/costs/breakeven?month={M}", org_id=a.org_id).status_code == 404
    assert report(b)["fixed"]["total_cents"] == 0


@pytest.mark.parametrize("payload", ["' OR '1'='1", "<script>alert(1)</script>"])
def test_query_injection_is_rejected(make_user, payload):
    api = make_user("a@x.com")
    report(api, categories=payload, expect=422)
