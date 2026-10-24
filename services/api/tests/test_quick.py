"""Plano direto (Gratuito): entitlements por papel e resumo com ideias para bater a meta."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Subscription
from tests.test_clinic import _setup
from tests.test_goals import goal

M = "2026-10"


def work(api, name="Plantão", gross=200_000, tax=None, hours=12, wtype="SHIFT", regime="PJ"):
    income = {"gross_cents": gross, "unit_type": "PER_EVENT", "duration_minutes": hours * 60}
    if tax is not None:
        income["tax_rate_bps"] = tax
    r = api.req(
        "POST",
        "/api/v1/work-sources",
        json={"name": name, "work_type": wtype, "regime": regime, "income": income},
    )
    assert r.status_code == 201, r.text
    return r.json()


def plan(api, kind, sid, units):
    r = api.req(
        "PUT",
        "/api/v1/income-plans",
        json={
            "month": None,
            "source_type": kind,
            "source_id": sid,
            "units": units,
            "certainty": "PROBABLE",
        },
    )
    assert r.status_code == 200, r.text


def cost(api, category="aluguel", amount=300_000):
    r = api.req(
        "POST",
        "/api/v1/recurrences",
        json={
            "description": category,
            "category": category,
            "amount_cents": amount,
            "frequency": "MONTHLY",
            "start_date": "2026-01-01",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def quick(api, expect=200):
    r = api.req("GET", f"/api/v1/quick/summary?month={M}")
    assert r.status_code == expect, r.text
    return r.json()


def end_trial(app):
    with app.state.session_factory() as s:
        row = s.scalar(select(Subscription))
        row.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        s.commit()


# ---------- entitlements ----------


def test_entitlements_trial_then_free_is_simple(make_user, app):
    api = make_user("a@x.com")
    e = api.req("GET", "/api/v1/billing/entitlements").json()
    assert (
        e["in_trial"]
        and e["effective_plan"] == "CLINIC"
        and e["simple"] is False
        and "assistant" in e["features"]
    )
    end_trial(app)
    e = api.req("GET", "/api/v1/billing/entitlements").json()
    assert e["simple"] is True and e["effective_plan"] == "FREE" and e["plan_name"] == "Gratuito"
    assert e["limits"]["works"] == 2 and e["limits"]["goals_per_month"] == 1 and e["features"] == []
    assert "payment" not in str(e).lower() and "token" not in str(e).lower()  # nada de cobrança


def test_entitlements_readable_by_every_member_but_tenant_scoped(make_user):
    owner, sec, other = make_user("o@x.com"), make_user("s@x.com"), make_user("x@x.com")
    owner.req("POST", "/api/v1/team/members", json={"email": "s@x.com", "role": "ASSISTANT"})
    assert sec.req("GET", "/api/v1/billing/entitlements", org_id=owner.org_id).status_code == 200
    assert other.req("GET", "/api/v1/billing/entitlements", org_id=owner.org_id).status_code == 404


# ---------- resumo ----------


def test_empty_account_asks_for_the_goal_first(make_user):
    s = quick(make_user("a@x.com"))
    assert (
        s["status"] == "NO_GOAL"
        and s["next_step"] == "GOAL"
        and s["sources"] == []
        and s["ideas"] == []
    )
    assert s["goal"] is None and s["tax_is_estimate"] is True and s["notes"]


def test_goal_without_sources_asks_for_sources(make_user):
    api = make_user("a@x.com")
    goal(api, 3_000_000, month=M)
    s = quick(api)
    assert (
        s["status"] == "NO_SOURCES" and s["next_step"] == "SOURCES" and s["gap_cents"] == 3_000_000
    )


def test_numbers_income_costs_result_and_gap(make_user):
    api = make_user("a@x.com")
    w = work(api, tax=1_500)  # R$ 2.000 bruto, 15% → R$ 1.700 líquidos por plantão
    plan(api, "WORK", w["id"], 4)
    cost(api, "aluguel", 300_000)
    cost(api, "secretaria", 200_000)
    goal(api, 1_000_000, month=M)
    s = quick(api)
    assert s["income_net_cents"] == 4 * 170_000 == 680_000
    assert (
        s["costs"]["total_cents"] == 500_000
        and len(s["costs"]["recurring"]) == 2
        and s["costs"]["other_cents"] == 0
    )
    assert s["result_cents"] == 180_000 and s["gap_cents"] == 820_000 and s["status"] == "SHORT"
    src = s["sources"][0]
    assert (src["units"], src["net_per_unit_cents"], src["minutes_per_unit"]) == (4, 170_000, 720)
    assert {c["label"] for c in s["costs"]["recurring"]} == {"Aluguel", "Secretaria e encargos"}


def test_met_goal_has_no_ideas(make_user):
    api = make_user("a@x.com")
    w = work(api, tax=0)
    plan(api, "WORK", w["id"], 5)
    goal(api, 500_000, month=M)
    s = quick(api)
    assert s["status"] == "MET" and s["gap_cents"] == 0 and s["ideas"] == []


def test_ideas_more_of_mix_and_cut_costs(make_user):
    api = make_user("a@x.com")
    shift = work(api, "Plantão", 200_000, tax=0, hours=12)  # R$ 2.000/12h
    oncall = work(api, "Sobreaviso", 100_000, tax=0, hours=6, wtype="ON_CALL")  # R$ 1.000/6h
    plan(api, "WORK", shift["id"], 2)
    plan(api, "WORK", oncall["id"], 2)
    cost(api, "aluguel", 100_000)
    goal(api, 1_000_000, month=M)  # resultado: 4.000 + 2.000 − 1.000 = R$ 5.000 → faltam R$ 5.000
    s = quick(api)
    assert s["result_cents"] == 500_000 and s["gap_cents"] == 500_000
    ideas = {i["kind"]: i for i in s["ideas"]}
    # os dois rendem o mesmo por hora (R$ 166,67/h): a ordem entre eles é só a estável da ordenação
    more = [i for i in s["ideas"] if i["kind"] == "MORE_OF"]
    assert len(more) == 2
    for i in more:
        per_unit = 200_000 if i["title"] == "Mais Plantão" else 100_000
        assert (
            i["units"] == -(-500_000 // per_unit)
            and i["extra_net_cents"] >= 500_000
            and i["closes_gap"]
        )
        assert i["extra_minutes"] == i["units"] * (720 if per_unit == 200_000 else 360)
    mix = ideas["MIX"]
    assert mix["extra_net_cents"] >= 500_000 and len(mix["units"]) == 2 and mix["closes_gap"]
    cut = ideas["CUT_COSTS"]
    assert (
        cut["cut_cents"] == 100_000 and cut["closes_gap"] is False and "não fecha" in cut["detail"]
    )


def test_cut_costs_can_close_a_small_gap(make_user):
    api = make_user("a@x.com")
    w = work(api, tax=0)
    plan(api, "WORK", w["id"], 2)  # R$ 4.000
    cost(api, "aluguel", 300_000)  # resultado R$ 1.000
    goal(api, 1_200_000 - 1_000_000 + 100_000, month=M)  # meta R$ 3.000 → faltam R$ 2.000
    s = quick(api)
    cut = next(i for i in s["ideas"] if i["kind"] == "CUT_COSTS")
    assert s["gap_cents"] == 200_000 and cut["cut_cents"] == 200_000 and cut["closes_gap"] is True


def test_consultation_services_appear_as_sources(make_user):
    api = make_user("a@x.com")
    _, svc = _setup(api)  # R$ 300 por consulta nova, 55,2 min com retorno gratuito
    plan(api, "SERVICE", svc["id"], 20)
    goal(api, 1_000_000, month=M)
    s = quick(api)
    src = s["sources"][0]
    assert src["kind"] == "SERVICE" and src["units"] == 20 and src["net_per_unit_cents"] == 30_000
    assert s["income_net_cents"] == 600_000 and s["status"] == "SHORT"
    assert s["ideas"][0]["units"] == -(-400_000 // 30_000)


def test_default_tax_flows_into_the_quick_numbers(make_user):
    api = make_user("a@x.com")
    w = work(api)  # sem retenção informada
    plan(api, "WORK", w["id"], 1)
    goal(api, 500_000, month=M)
    assert quick(api)["income_net_cents"] == 200_000
    api.req(
        "PUT",
        "/api/v1/tax/settings",
        json={"enabled": True, "pf_bps": 2_700, "pj_bps": 1_500, "clinic_regime": "PJ"},
    )
    s = quick(api)
    assert s["income_net_cents"] == 170_000 and s["sources"][0]["tax_source"] == "PADRAO"


def test_removed_source_and_cost_disappear(make_user):
    api = make_user("a@x.com")
    w = work(api, tax=0)
    plan(api, "WORK", w["id"], 2)
    c = cost(api)
    goal(api, 1_000_000, month=M)
    assert quick(api)["costs"]["total_cents"] == 300_000
    assert api.req("DELETE", f"/api/v1/recurrences/{c['id']}").status_code == 204
    assert api.req("DELETE", f"/api/v1/work-sources/{w['id']}").status_code == 204
    s = quick(api)
    assert s["sources"] == [] and s["costs"]["total_cents"] == 0 and s["status"] == "NO_SOURCES"


def test_other_expenses_are_counted_but_not_removable_here(make_user):
    api = make_user("a@x.com")
    api.req(
        "POST",
        "/api/v1/transactions",
        json={
            "type": "EXPENSE",
            "status": "REALIZED",
            "competence_month": M,
            "paid_at": "2026-10-05",
            "amount_cents": 80_000,
            "category": "material",
            "description": "Material",
        },
    )
    cost(api, "aluguel", 300_000)
    s = quick(api)
    assert (
        s["costs"]["total_cents"] == 380_000
        and s["costs"]["other_cents"] == 80_000
        and len(s["costs"]["recurring"]) == 1
    )


def test_yearly_recurrences_are_not_listed_as_monthly_costs(make_user):
    api = make_user("a@x.com")
    api.req(
        "POST",
        "/api/v1/recurrences",
        json={
            "description": "Anual",
            "category": "software",
            "amount_cents": 120_000,
            "frequency": "YEARLY",
            "start_date": "2026-01-01",
        },
    )
    assert quick(api)["costs"]["recurring"] == []


# ---------- permissões e isolamento ----------


def test_permissions_and_isolation(make_user):
    owner, sec, acc, other = (make_user(f"{n}@x.com") for n in ("o", "s", "c", "x"))
    for u, role in ((sec, "ASSISTANT"), (acc, "ACCOUNTANT")):
        owner.req(
            "POST",
            "/api/v1/team/members",
            json={"email": u.http.get("/api/v1/me").json()["email"], "role": role},
        )
    w = work(owner, tax=0)
    plan(owner, "WORK", w["id"], 2)
    assert (
        sec.req("GET", f"/api/v1/quick/summary?month={M}", org_id=owner.org_id).status_code == 403
    )  # sem metas/rendas pessoais
    assert (
        acc.req("GET", f"/api/v1/quick/summary?month={M}", org_id=owner.org_id).status_code == 403
    )
    assert (
        other.req("GET", f"/api/v1/quick/summary?month={M}", org_id=owner.org_id).status_code == 404
    )
    assert quick(other)["sources"] == []


@pytest.mark.parametrize("bad", ["2026-13", "2026-1", "abc", "2026-10' OR '1'='1"])
def test_month_is_validated(make_user, bad):
    api = make_user("a@x.com")
    assert api.req("GET", f"/api/v1/quick/summary?month={bad}").status_code == 422


def test_read_only(make_user, app):
    from sqlalchemy import func

    from app.db.models import MonthlyGoal, Transaction, WorkSource

    api = make_user("a@x.com")
    w = work(api)
    plan(api, "WORK", w["id"], 2)
    goal(api, 1_000_000, month=M)

    def counts():
        with app.state.session_factory() as s:
            return [
                s.scalar(select(func.count()).select_from(t))
                for t in (WorkSource, MonthlyGoal, Transaction)
            ]

    before = counts()
    quick(api)
    assert counts() == before
