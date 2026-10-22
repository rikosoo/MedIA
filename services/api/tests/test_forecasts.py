"""Sprint 8: previsão 3/6/12 meses com premissas explícitas e faixa de incerteza."""

from datetime import date

import pytest

from finance_engine.forecast import inflate, working_days_factor
from tests.conftest import SHIFT
from tests.test_goals import goal, tx

AS_OF = "2026-10-15"  # a previsão começa em novembro
SHIFT_NET = 170_000  # R$ 2.000 - 15% por plantão


def run(api, expect=200, **kw):
    r = api.req("POST", "/api/v1/forecasts/run", json={"as_of": AS_OF, "horizon_months": 3, **kw})
    assert r.status_code == expect, r.text
    return r.json()


def default_plan(api, units=4, certainty="GUARANTEED", work=None):
    w = work or api.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    r = api.req("PUT", "/api/v1/income-plans", json={"month": None, "source_type": "WORK",
        "source_id": w["id"], "units": units, "certainty": certainty})  # fmt: skip
    assert r.status_code == 200, r.text
    return w


def income(api, month, amount):
    tx(api, type="INCOME", category="consultas", competence_month=month, paid_at=f"{month}-10",
       amount_cents=amount, description=f"Receita {month}")  # fmt: skip


def history(api):
    """Jul 80%, ago 100%, set 90% do planejado bruto (4 plantões x R$ 2.000 = R$ 8.000)."""
    for mo, v in (("2026-07", 640_000), ("2026-08", 800_000), ("2026-09", 720_000)):
        income(api, mo, v)


def month(res, m):
    return next(x for x in res["months"] if x["month"] == m)


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    assert (
        owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role}).status_code
        == 201
    )


def test_without_history_uses_declared_manual_assumptions(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    res = run(api)
    assert res["from_month"] == "2026-11" and [m["month"] for m in res["months"]] == [
        "2026-11",
        "2026-12",
        "2027-01",
    ]
    a = res["assumptions"]["attainment"]
    assert a["source"] == "MANUAL" and (
        a["conservative_bps"],
        a["base_bps"],
        a["optimistic_bps"],
    ) == (8000, 10_000, 10_000)
    assert res["data_quality"] == "manual_assumptions"
    dec = month(res, "2026-12")
    assert dec["plan_income_cents"] == 4 * SHIFT_NET
    assert dec["income_cents"] == {"conservative": 544_000, "base": 680_000, "optimistic": 680_000}
    assert dec["net_cents"] == dec["income_cents"]  # sem despesas
    assert any("Poucos meses de histórico" in n for n in res["assumptions"]["notes"])
    assert any("não uma promessa" in n for n in res["assumptions"]["notes"])


def test_history_drives_the_three_scenarios(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    history(api)
    income(api, "2026-10", 50_000)  # mês em curso: incompleto, não conta
    res = run(api)
    a = res["assumptions"]["attainment"]
    assert (a["source"], a["months_used"]) == ("HISTORY", 3)
    assert (a["conservative_bps"], a["base_bps"], a["optimistic_bps"]) == (8000, 9000, 10_000)
    assert [h["month"] for h in a["history"]] == ["2026-07", "2026-08", "2026-09"]
    dec = month(res, "2026-12")
    inc = dec["income_cents"]
    assert inc == {
        "conservative": 680_000 * 8000 // 10_000,
        "base": 680_000 * 9000 // 10_000,
        "optimistic": 680_000,
    }
    assert inc["conservative"] < inc["base"] < inc["optimistic"] + 1
    assert res["data_quality"] == "history"


def test_months_without_any_entry_do_not_count_as_zero_percent(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    history(api)  # jun/mai sem lançamentos: o app ainda não era usado
    a = run(api)["assumptions"]["attainment"]
    assert a["conservative_bps"] == 8000 and a["months_used"] == 3


def test_holidays_and_vacation_reduce_per_event_income_not_fixed_salary(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    clt = api.req("POST", "/api/v1/work-sources", json={**SHIFT, "name": "CLT", "work_type": "EMPLOYMENT",
        "regime": "CLT", "income": {"gross_cents": 1_000_000, "unit_type": "MONTHLY", "duration_minutes": 0,
        "tax_rate_bps": 0}}).json()  # fmt: skip
    default_plan(api, units=1, work=clt)
    for d, name in (("2026-11-02", "Finados"), ("2026-11-20", "Consciência Negra")):
        api.req("POST", "/api/v1/holidays", json={"date": d, "name": name, "scope": "NATIONAL"})
    api.req(
        "POST", "/api/v1/holidays", json={"date": "2026-11-15", "name": "Domingo"}
    )  # fim de semana: não perde
    api.req(
        "POST",
        "/api/v1/leaves",
        json={"staff_role": "PHYSICIAN", "start_date": "2026-11-09", "end_date": "2026-11-13"},
    )
    nov = month(run(api), "2026-11")
    f, total, lost = working_days_factor(
        2026, 11, {date(2026, 11, 2), date(2026, 11, 20)}, {date(2026, 11, d) for d in range(9, 14)}
    )
    assert (nov["working_days"], nov["working_days_lost"], nov["working_days_factor_bps"]) == (
        total,
        lost,
        f,
    ) and lost == 7
    assert (
        nov["plan_income_cents"] == 4 * SHIFT_NET * f // 10_000 + 1_000_000
    )  # CLT mensal fixo intacto
    assert any(
        "7 dia(s) útil(eis) a menos" in d and "Finados" in d and "férias" in d
        for d in nov["drivers"]
    )
    assert month(run(api), "2026-12")["working_days_lost"] == 0


def test_unconfirmed_holiday_suggestion_does_not_reduce_capacity(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    api.req("POST", "/api/v1/holidays/suggest?year=2026")  # sugeridos, não confirmados
    assert month(run(api), "2026-11")["working_days_lost"] == 0


def test_expenses_use_the_larger_of_planned_and_inflated_baseline(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    for mo in ("2026-07", "2026-08", "2026-09"):
        tx(
            api,
            competence_month=mo,
            paid_at=f"{mo}-05",
            amount_cents=100_000,
            description=f"Aluguel {mo}",
        )
    api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED", "competence_month": "2026-11",
        "amount_cents": 300_000, "category": "equipamentos", "description": "Compra"})  # fmt: skip
    res = run(api, expense_inflation_bps=100)
    assert res["assumptions"]["expense_baseline_cents"] == 100_000
    assert (
        month(res, "2026-11")["expenses_cents"] == 300_000
    )  # planejado maior que a base corrigida
    assert month(res, "2026-12")["expenses_cents"] == inflate(100_000, 100, 2) == 102_010
    assert month(res, "2027-01")["expenses_cents"] == inflate(100_000, 100, 3)
    flat = run(api, expense_inflation_bps=0)
    assert month(flat, "2026-12")["expenses_cents"] == 100_000
    nov = month(res, "2026-12")
    assert nov["net_cents"]["base"] == nov["income_cents"]["base"] - 102_010


def test_manual_adjustments_are_explicit_drivers(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    res = run(api, adjustments=[{"month": "2026-12", "label": "Curso", "expense_cents": 500_000},
                                {"month": "2026-12", "label": "Bônus", "income_delta_cents": 200_000}])  # fmt: skip
    dec = month(res, "2026-12")
    assert dec["expenses_cents"] == 500_000 and dec["income_cents"]["base"] == 680_000 + 200_000
    assert dec["net_cents"]["base"] == 880_000 - 500_000
    assert "Ajuste manual: Curso." in dec["drivers"] and "Ajuste manual: Bônus." in dec["drivers"]
    assert res["assumptions"]["adjustments"][0]["label"] == "Curso"
    assert "Ajuste manual: Curso." not in month(res, "2026-11")["drivers"]


def test_seasonality_needs_twelve_months_and_is_opt_in(make_user):
    api = make_user("a@x.com")
    svc = api.req(
        "POST",
        "/api/v1/clinic-services",
        json={"name": "Consulta", "price_cents": 30_000, "duration_minutes": 40},
    ).json()
    api.req("PUT", "/api/v1/income-plans", json={"month": None, "source_type": "SERVICE", "source_id": svc["id"], "units": 10,
        "certainty": "PROBABLE"})  # fmt: skip
    few = run(api, use_seasonality=True)
    assert few["assumptions"]["seasonality"]["applied"] is False
    assert any("Sazonalidade não aplicada" in n for n in few["assumptions"]["notes"])
    for mo in (
        "2025-10",
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
    ):
        income(api, mo, 200_000 if mo == "2025-12" else 100_000)
    off = run(api)
    assert (
        off["assumptions"]["seasonality"]["applied"] is False
        and off["assumptions"]["seasonality"]["requested"] is False
    )
    on = run(api, use_seasonality=True)
    s = on["assumptions"]["seasonality"]
    assert (
        s["applied"]
        and s["history_months"] == 12
        and s["indices_bps"]["12"] > 10_000 > s["indices_bps"]["11"]
    )
    nov_on, dec_on = (
        month(on, "2026-11")["plan_income_cents"],
        month(on, "2026-12")["plan_income_cents"],
    )
    nov_off, dec_off = (
        month(off, "2026-11")["plan_income_cents"],
        month(off, "2026-12")["plan_income_cents"],
    )
    assert nov_off == dec_off == 300_000
    assert (
        dec_on > nov_on
        and dec_on == 300_000 * s["indices_bps"]["12"] // 10_000
        and nov_on < 300_000
    )


def test_reserve_for_months_below_the_floor(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    res = run(api, floor_cents=600_000)  # base 680.000 (ok); conservador 544.000 (56.000 abaixo)
    assert res["reserve"]["base"] == {"reserve_cents": 0, "months_below_floor": 0}
    assert res["reserve"]["conservative"] == {"reserve_cents": 3 * 56_000, "months_below_floor": 3}
    assert res["totals"]["conservative"] == 3 * 544_000
    assert run(api)["reserve"] is None  # sem piso informado, sem reserva inventada


def test_goal_is_shown_and_months_without_plan_are_flagged(make_user):
    api = make_user("a@x.com")
    goal(api, month="2026-12", target_cents=900_000)
    res = run(api)
    assert (
        all(m["flags"] == ["SEM_PLANO"] for m in res["months"])
        and month(res, "2026-12")["plan_income_cents"] == 0
    )
    assert (
        month(res, "2026-12")["goal_target_cents"] == 900_000
        and month(res, "2026-11")["goal_target_cents"] is None
    )


def test_month_specific_plan_overrides_the_default(make_user):
    api = make_user("a@x.com")
    w = default_plan(api, units=4)
    api.req("PUT", "/api/v1/income-plans", json={"month": "2026-12", "source_type": "WORK", "source_id": w["id"],
        "units": 2, "certainty": "PROBABLE"})  # fmt: skip
    res = run(api)
    assert month(res, "2026-11")["plan_income_cents"] == 4 * SHIFT_NET
    assert month(res, "2026-12")["plan_income_cents"] == 2 * SHIFT_NET


def test_secretary_leave_is_listed_as_a_driver(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    api.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-12-01", "end_date": "2026-12-15",
        "coverage_factor_bps": 0})  # fmt: skip
    dec = month(run(api), "2026-12")
    assert dec["secretary_coverage_bps"] < 10_000 and any("secretária" in d for d in dec["drivers"])


@pytest.mark.parametrize("horizon", [3, 6, 12])
def test_horizons(make_user, horizon):
    api = make_user("a@x.com")
    default_plan(api)
    res = run(api, horizon_months=horizon)
    assert len(res["months"]) == horizon and res["horizon_months"] == horizon
    assert (
        res["months"][-1]["month"] == ["2027-01", "2027-04", "2027-10"][[3, 6, 12].index(horizon)]
    )


def test_deterministic_and_does_not_persist_by_default(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    history(api)
    assert run(api) == run(api)
    assert api.req("GET", "/api/v1/forecasts").json() == []


def test_save_list_get_delete_snapshot(make_user):
    api = make_user("a@x.com")
    w = default_plan(api)
    res = run(api, save=True, name="Cenário base", floor_cents=500_000)
    fid = res["saved_id"]
    lst = api.req("GET", "/api/v1/forecasts").json()
    assert (
        lst[0]["id"] == fid
        and lst[0]["name"] == "Cenário base"
        and lst[0]["from_month"] == "2026-11"
        and lst[0]["to_month"] == "2027-01"
    )
    api.req("PUT", "/api/v1/income-plans", json={"month": None, "source_type": "WORK", "source_id": w["id"], "units": 8,
        "certainty": "GUARANTEED"})  # fmt: skip
    snap = api.req("GET", f"/api/v1/forecasts/{fid}").json()
    assert (
        snap["months"][0]["plan_income_cents"] == 4 * SHIFT_NET
    )  # congelado: mudar o plano não altera
    assert snap["assumptions"]["floor_cents"] == 500_000 and snap["id"] == fid
    assert api.req("DELETE", f"/api/v1/forecasts/{fid}").status_code == 204
    assert (
        api.req("GET", f"/api/v1/forecasts/{fid}").status_code == 404
        and api.req("GET", "/api/v1/forecasts").json() == []
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"horizon_months": 4},
        {"horizon_months": 0},
        {"expense_inflation_bps": 501},
        {"floor_cents": -1},
        {"manual": {"conservative_bps": 9_000, "base_bps": 8_000, "optimistic_bps": 10_000}},
        {"manual": {"conservative_bps": 8_000, "base_bps": 10_000, "optimistic_bps": 13_000}},
        {"adjustments": [{"month": "2026-13", "label": "x"}]},
        {"adjustments": [{"month": "2026-12", "label": ""}]},
        {"from_month": "2026-09"},
    ],
)
def test_invalid_requests(make_user, bad):
    api = make_user("a@x.com")
    default_plan(api)
    assert (
        api.req(
            "POST", "/api/v1/forecasts/run", json={"as_of": AS_OF, "horizon_months": 3, **bad}
        ).status_code
        == 422
    )


def test_forecast_can_start_later_but_not_in_the_past(make_user):
    api = make_user("a@x.com")
    default_plan(api)
    later = run(api, from_month="2027-02")
    assert later["months"][0]["month"] == "2027-02"
    assert (
        run(api, from_month="2026-10")["months"][0]["month"] == "2026-10"
    )  # o mês atual ainda vale


# ---------- isolamento e papéis ----------


def test_cross_tenant_and_personal_forecasts(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    default_plan(a)
    saved = run(a, save=True)["saved_id"]
    rb = run(b)
    assert all(m["plan_income_cents"] == 0 for m in rb["months"])  # B não enxerga os planos de A
    assert b.req("GET", f"/api/v1/forecasts/{saved}").status_code == 404
    assert b.req("DELETE", f"/api/v1/forecasts/{saved}").status_code == 404
    assert b.req("GET", "/api/v1/forecasts").json() == []
    assert b.req("GET", "/api/v1/forecasts", org_id=a.org_id).status_code == 404


def test_forecasts_are_personal_even_for_the_owner(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _member(owner, doc, "PHYSICIAN")
    r = doc.req(
        "POST", "/api/v1/forecasts/run", org_id=owner.org_id, json={"as_of": AS_OF, "save": True}
    )
    assert r.status_code == 200
    assert owner.req("GET", f"/api/v1/forecasts/{r.json()['saved_id']}").status_code == 404
    assert owner.req("GET", "/api/v1/forecasts").json() == []


@pytest.mark.parametrize("role", ["ASSISTANT", "ACCOUNTANT", "MANAGER"])
def test_roles_without_personal_income_cannot_forecast(make_user, role):
    owner, other = make_user("o@x.com"), make_user("x@x.com")
    _member(owner, other, role)
    default_plan(owner)
    assert (
        other.req(
            "POST", "/api/v1/forecasts/run", org_id=owner.org_id, json={"as_of": AS_OF}
        ).status_code
        == 403
    )
    assert other.req("GET", "/api/v1/forecasts", org_id=owner.org_id).status_code == 403
