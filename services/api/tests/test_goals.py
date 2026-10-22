"""Sprint 4: metas, composição do mês, lacuna auditável e dashboard (US-003)."""

import pytest

from tests.conftest import SHIFT
from tests.test_clinic import POLICY, SERVICE

M = "2026-10"


def setup_month(api, work_units=8, service_units=50):
    """Cenário do PRD: renda garantida de plantões + pacientes novos prováveis + despesas."""
    w = api.req("POST", "/api/v1/work-sources", json=SHIFT).json()  # R$ 2.000, 15% de retenção
    p = api.req("POST", "/api/v1/return-policies", json=POLICY).json()
    s = api.req(
        "POST", "/api/v1/clinic-services", json={**SERVICE, "return_policy_id": p["id"]}
    ).json()
    plan(api, "WORK", w["id"], work_units, "GUARANTEED")
    plan(api, "SERVICE", s["id"], service_units, "PROBABLE", tax_rate_bps=1000)
    return w, s


def plan(api, kind, sid, units, certainty, month=M, expect=200, **kw):
    r = api.req(
        "PUT",
        "/api/v1/income-plans",
        json={"month": month, "source_type": kind, "source_id": sid, "units": units,
              "certainty": certainty, **kw},
    )  # fmt: skip
    assert r.status_code == expect, r.text
    return r.json()


def tx(api, **over):
    body = {"type": "EXPENSE", "status": "REALIZED", "competence_month": M, "paid_at": "2026-10-05",
            "amount_cents": 250_000, "category": "aluguel", "description": "Aluguel"}  # fmt: skip
    r = api.req("POST", "/api/v1/transactions", json={**body, **over})
    assert r.status_code == 201, r.text


def expenses(api):
    tx(api)  # realizado 2.500
    tx(
        api,
        status="PLANNED",
        paid_at=None,
        amount_cents=90_000,
        category="software",
        description="Sistema",
    )
    api.req("POST", "/api/v1/recurrences", json={"description": "Internet", "category": "utilidades",
            "amount_cents": 20_000, "frequency": "MONTHLY", "start_date": "2026-01-15"})  # fmt: skip


def goal(api, target=3_000_000, metric="PERSONAL_NET", month=M, **kw):
    r = api.req("POST", "/api/v1/goals", json={"month": month, "metric_type": metric,
                                               "target_cents": target, **kw})  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def dash(api, month=M, **q):
    qs = "&".join(f"{k}={v}" for k, v in {"month": month, **q}.items())
    r = api.req("GET", f"/api/v1/dashboard?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def full_scenario(api):
    setup_month(api)
    expenses(api)
    tx(api, type="INCOME", category="consultas", amount_cents=1_000_000, description="Consultas")


def test_gap_is_auditable_end_to_end(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    g = goal(api)
    gap = api.req("GET", f"/api/v1/goals/{g['id']}/gap").json()
    # 13.600 (plantões) + 13.500 (consultas) − 3.600 (despesas) = 23.500
    assert gap["projected_cents"] == 2_350_000
    assert gap["assured_cents"] == 1_000_000 and gap["probable_cents"] == 1_350_000
    assert gap["gap_cents"] == 650_000 and gap["assured_gap_cents"] == 2_000_000
    assert gap["realized_cents"] == 750_000 and gap["realized_gap_cents"] == 2_250_000
    assert gap["status"] == "SHORT"
    by_kind = {b["kind"]: b for b in gap["breakdown"]}
    w, s = by_kind["WORK"], by_kind["SERVICE"]
    assert (w["gross_cents"], w["tax_cents"], w["net_cents"]) == (1_600_000, 240_000, 1_360_000)
    assert (s["gross_cents"], s["tax_cents"], s["net_cents"]) == (1_500_000, 150_000, 1_350_000)
    assert s["active_minutes"] == 2760  # 50 pacientes x 55,2 min
    assert w["certainty"] == "GUARANTEED" and s["certainty"] == "PROBABLE"
    # a lacuna nunca esconde o déficit: meta − projetado
    assert gap["effective_target_cents"] - gap["projected_cents"] == gap["gap_cents"]


def test_planned_is_not_realized_and_not_added_to_it(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    d = dash(api)
    assert d["projection"]["value_cents"] == 2_350_000  # plano, sem somar o realizado
    assert d["realized"] == {
        "income_cents": 1_000_000,
        "expense_cents": 250_000,
        "value_cents": 750_000,
    }
    assert d["expenses_detail"]["realized_cents"] == 250_000
    assert (
        d["expenses_detail"]["planned_cents"] == 90_000 + 20_000
    )  # planejada + recorrência projetada
    assert any("nunca é somado" in a for a in d["assumptions"])


@pytest.mark.parametrize(
    "metric,expected",
    [
        ("PERSONAL_NET", 2_350_000),
        ("CLINIC_PROFIT", 1_350_000 - 360_000),
        ("GROSS_REVENUE", 3_100_000),
    ],
)
def test_metric_types_are_separate(make_user, metric, expected):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api, metric=metric)
    d = dash(api)
    assert d["metric_type"] == metric and d["projection"]["value_cents"] == expected
    assert d["goal"]["projected_cents"] == expected


def test_clinic_realized_ignores_non_clinic_income(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    tx(
        api,
        type="INCOME",
        category="plantoes",
        amount_cents=500_000,
        description="Plantão pago",
        paid_at="2026-10-08",
    )
    goal(api, metric="CLINIC_PROFIT")
    assert dash(api)["realized"]["value_cents"] == 1_000_000 - 250_000  # só consultas
    pn = api.req(
        "POST", "/api/v1/goals", json={"month": M, "metric_type": "PERSONAL_NET", "target_cents": 1}
    )
    assert pn.status_code == 201
    assert dash(api)["realized"]["value_cents"] == 1_500_000 - 250_000  # PERSONAL_NET é preferido


def test_reserves_are_off_by_default(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    g = goal(api, reserve_cents=500_000)
    gap = api.req("GET", f"/api/v1/goals/{g['id']}/gap").json()
    assert gap["effective_target_cents"] == 3_000_000 and gap["gap_cents"] == 650_000
    api.req("PATCH", f"/api/v1/goals/{g['id']}", json={"use_reserves": True})
    gap = api.req("GET", f"/api/v1/goals/{g['id']}/gap").json()
    assert gap["effective_target_cents"] == 2_500_000 and gap["gap_cents"] == 150_000


def test_goal_met_recommends_stability_and_zero_gap(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api, target=2_000_000)
    d = dash(api)
    assert d["goal"]["status"] == "MET" and d["goal"]["gap_cents"] == 0
    assert any(a["code"] == "MET" and "estabilidade" in a["message"] for a in d["alerts"])


def test_met_but_schedule_infeasible_is_not_plain_met(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api, target=2_000_000)
    for h in ("08:00", "08:20"):  # dois atendimentos sobrepostos
        r = api.req("POST", "/api/v1/schedule/events", json={
            "type": "CONSULT_NEW", "starts_at": f"2026-10-06T{h}:00-03:00",
            "ends_at": f"2026-10-06T{'08:40' if h == '08:00' else '09:00'}:00-03:00",
            "allow_conflict": True})  # fmt: skip
        assert r.status_code == 201
    d = dash(api)
    assert d["goal"]["status"] == "MET_WITH_ISSUES" and d["goal"]["schedule_feasible"] is False
    assert any(a["code"] == "SCHEDULE" for a in d["alerts"])
    assert d["hours"]["schedule_feasible"] is False


def test_deficit_is_shown_with_sign(make_user):
    api = make_user("a@x.com")
    tx(api)  # só despesa de 2.500
    goal(api, target=1_000_000)
    d = dash(api)
    assert d["projection"]["value_cents"] == -250_000
    assert d["goal"]["gap_cents"] == 1_250_000
    assert any(a["code"] == "DEFICIT" for a in d["alerts"])
    assert d["data_quality"] == "insufficient" and d["data_quality_reasons"]


def test_no_goal_and_no_data_states(make_user):
    api = make_user("a@x.com")
    d = dash(api)
    assert d["goal"] is None and d["projection"]["value_cents"] == 0 and d["breakdown"] == []
    assert d["data_quality"] == "insufficient"


def test_default_plan_is_inherited_and_month_overrides(make_user):
    api = make_user("a@x.com")
    w = api.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    plan(api, "WORK", w["id"], 4, "GUARANTEED", month=None)
    assert dash(api, "2026-10")["projection"]["income_net_cents"] == 4 * 170_000
    assert dash(api, "2026-11")["projection"]["income_net_cents"] == 4 * 170_000
    plan(api, "WORK", w["id"], 2, "PROBABLE", month="2026-11")
    nov = dash(api, "2026-11")
    assert (
        nov["projection"]["income_net_cents"] == 2 * 170_000
        and nov["projection"]["assured_cents"] == 0
    )
    assert dash(api, "2026-10")["projection"]["income_net_cents"] == 4 * 170_000
    listing = api.req("GET", "/api/v1/income-plans?month=2026-12").json()
    assert listing[0]["plan"]["inherited"] is True and listing[0]["plan"]["units"] == 4


def test_plan_upsert_does_not_duplicate_income(make_user):
    api = make_user("a@x.com")
    w = api.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    a = plan(api, "WORK", w["id"], 2, "GUARANTEED")
    b = plan(api, "WORK", w["id"], 5, "GUARANTEED")
    assert a["id"] == b["id"]
    assert dash(api)["projection"]["income_net_cents"] == 5 * 170_000


def test_clinic_work_source_cannot_be_planned_twice(make_user):
    """PRD 6.4: o consultório não pode ser contado como trabalho *e* como serviço."""
    api = make_user("a@x.com")
    clinic = {**SHIFT, "name": "Consultório", "work_type": "CLINIC"}
    w = api.req("POST", "/api/v1/work-sources", json=clinic).json()
    plan(api, "WORK", w["id"], 1, "GUARANTEED", expect=422)
    el = [
        x
        for x in api.req("GET", f"/api/v1/income-plans?month={M}").json()
        if x["source_id"] == w["id"]
    ]
    assert el[0]["eligible"] is False


def test_archived_and_deleted_sources_leave_the_projection(make_user):
    api = make_user("a@x.com")
    w, s = setup_month(api)
    api.req("PATCH", f"/api/v1/work-sources/{w['id']}", json={"status": "archived"})
    api.req("DELETE", f"/api/v1/clinic-services/{s['id']}")
    assert dash(api)["breakdown"] == []


def test_goal_crud_duplicate_and_uniqueness(make_user):
    api = make_user("a@x.com")
    g = goal(api)
    assert api.req("POST", "/api/v1/goals", json={"month": M, "target_cents": 1}).status_code == 409
    assert goal(api, metric="GROSS_REVENUE")["metric_type"] == "GROSS_REVENUE"
    dup = api.req("POST", f"/api/v1/goals/{g['id']}/duplicate", json={"month": "2026-11"})
    assert dup.status_code == 201 and dup.json()["target_cents"] == g["target_cents"]
    assert (
        api.req("POST", f"/api/v1/goals/{g['id']}/duplicate", json={"month": "2026-11"}).status_code
        == 409
    )
    hist = api.req("GET", "/api/v1/goals").json()
    assert [x["month"] for x in hist] == ["2026-11", "2026-10", "2026-10"]
    assert api.req("DELETE", f"/api/v1/goals/{g['id']}").status_code == 204
    assert len(api.req("GET", f"/api/v1/goals?month={M}").json()) == 1
    goal(api)  # após excluir, o mês pode receber nova meta


@pytest.mark.parametrize(
    "bad",
    [{"target_cents": -1}, {"target_cents": 1.5}, {"month": "2026-13"}, {"metric_type": "X"},
     {"priority": "X"}, {"reserve_cents": -5}],
)  # fmt: skip
def test_invalid_goal_rejected(make_user, bad):
    api = make_user("a@x.com")
    body = {"month": M, "metric_type": "PERSONAL_NET", "target_cents": 100, **bad}
    assert api.req("POST", "/api/v1/goals", json=body).status_code == 422


def test_invalid_plan_rejected(make_user):
    api = make_user("a@x.com")
    w = api.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    for bad in (
        {"units": -1},
        {"certainty": "MAYBE"},
        {"tax_rate_bps": 10_001},
        {"source_type": "X"},
    ):
        body = {"month": M, "source_type": "WORK", "source_id": w["id"], "units": 1,
                "certainty": "GUARANTEED", **bad}  # fmt: skip
        assert api.req("PUT", "/api/v1/income-plans", json=body).status_code == 422


def test_series_has_twelve_months_ending_at_requested(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api)
    s = dash(api)["series"]
    assert len(s) == 12 and s[0]["month"] == "2025-11" and s[-1]["month"] == M
    assert s[-1]["projected_cents"] == 2_350_000 and s[-1]["realized_cents"] == 750_000
    assert s[-1]["target_cents"] == 3_000_000 and s[-2]["target_cents"] is None
    assert s[0]["projected_cents"] == 0


# ---------- isolamento e privacidade ----------


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    assert (
        owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role}).status_code
        == 201
    )


def test_cross_tenant_goals_and_plans(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    w, s = setup_month(a)
    g = goal(a)
    assert b.req("GET", f"/api/v1/goals/{g['id']}/gap").status_code == 404
    assert b.req("PATCH", f"/api/v1/goals/{g['id']}", json={"target_cents": 1}).status_code == 404
    assert b.req("DELETE", f"/api/v1/goals/{g['id']}").status_code == 404
    assert (
        b.req("POST", f"/api/v1/goals/{g['id']}/duplicate", json={"month": "2026-12"}).status_code
        == 404
    )
    assert b.req("GET", "/api/v1/goals").json() == []
    plan(b, "WORK", w["id"], 1, "GUARANTEED", expect=422)
    plan(b, "SERVICE", s["id"], 1, "PROBABLE", expect=422)
    p = a.req("GET", f"/api/v1/income-plans?month={M}").json()[0]["plan"]
    assert b.req("DELETE", f"/api/v1/income-plans/{p['id']}").status_code == 404
    d = dash(b)
    assert d["breakdown"] == [] and d["projection"]["value_cents"] == 0
    assert b.req("GET", f"/api/v1/dashboard?month={M}", org_id=a.org_id).status_code == 404


def test_goals_are_personal_even_for_the_owner(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _member(owner, doc, "PHYSICIAN")
    g = goal(doc, month=M)  # meta criada pelo médico, na organização do dono
    mine = doc.req("GET", "/api/v1/goals", org_id=owner.org_id)
    assert mine.status_code == 200
    created = doc.req(
        "POST", "/api/v1/goals", org_id=owner.org_id, json={"month": M, "target_cents": 5}
    )
    assert created.status_code == 201
    assert owner.req("GET", "/api/v1/goals").json() == []  # o dono não vê a meta do médico
    gid = created.json()["id"]
    assert owner.req("GET", f"/api/v1/goals/{gid}/gap").status_code == 404
    assert g["id"] != gid


def test_other_users_work_is_not_in_my_projection(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _member(owner, doc, "PHYSICIAN")
    w = owner.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    plan(owner, "WORK", w["id"], 3, "GUARANTEED")
    plan(doc, "WORK", w["id"], 3, "GUARANTEED", expect=422)  # não é dele
    d = doc.req("GET", f"/api/v1/dashboard?month={M}", org_id=owner.org_id).json()
    assert d["breakdown"] == []


@pytest.mark.parametrize("role", ["ASSISTANT", "ACCOUNTANT", "MANAGER"])
def test_roles_without_personal_income_access(make_user, role):
    owner, other = make_user("o@x.com"), make_user("x@x.com")
    _member(owner, other, role)
    full_scenario(owner)
    goal(owner)
    for method, url in [
        ("GET", f"/api/v1/dashboard?month={M}"),
        ("GET", "/api/v1/goals"),
        ("GET", f"/api/v1/income-plans?month={M}"),
    ]:
        assert other.req(method, url, org_id=owner.org_id).status_code == 403, (role, url)
    r = other.req(
        "POST", "/api/v1/goals", org_id=owner.org_id, json={"month": M, "target_cents": 1}
    )
    assert r.status_code == 403
