"""Sprint 5: otimizador misto ponta a ponta (cenários, inviabilidade, aplicar/desfazer)."""

import pytest

from tests.conftest import SHIFT
from tests.test_clinic import POLICY, SERVICE

M = "2026-10"
ON_CALL = {
    "name": "Sobreaviso",
    "work_type": "ON_CALL",
    "regime": "PJ",
    "income": {
        "gross_cents": 100_000, "unit_type": "PER_EVENT", "duration_minutes": 120,
        "commitment_minutes": 720, "tax_rate_bps": 1500,
    },
}  # fmt: skip
SHIFT_DAYS = ["2026-10-06", "2026-10-13", "2026-10-20"]  # terças
ONCALL_DAYS = ["2026-10-07", "2026-10-14", "2026-10-21"]


def setup(api, with_availability=True):
    shift = api.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    oncall = api.req("POST", "/api/v1/work-sources", json=ON_CALL).json()
    pol = api.req("POST", "/api/v1/return-policies", json=POLICY).json()
    svc = api.req(
        "POST", "/api/v1/clinic-services", json={**SERVICE, "return_policy_id": pol["id"]}
    ).json()
    if with_availability:
        r = api.req("POST", "/api/v1/availability/weekly", json={
            "weekdays": [0, 1, 2, 3, 4], "start_time": "08:00", "end_time": "12:00",
            "from_date": "2026-10-01", "to_date": "2026-10-31"})  # fmt: skip
        assert r.status_code == 201
    return shift, oncall, svc


def acts(shift, oncall, svc, shift_max=3, oncall_max=3, consult_max=10, demand=10, **kw):
    return [
        {"source_type": "WORK", "source_id": shift["id"], "max_units": shift_max,
         "candidate_days": SHIFT_DAYS},
        {"source_type": "WORK", "source_id": oncall["id"], "max_units": oncall_max,
         "candidate_days": ONCALL_DAYS},
        {"source_type": "SERVICE", "source_id": svc["id"], "max_units": consult_max,
         "demand_limit": demand, **kw},
    ]  # fmt: skip


def run(api, activities, target=5_000_000, baseline=3_800_000, expect=201, **kw):
    body = {
        "month": M,
        "target_cents": target,
        "baseline_cents": baseline,
        "activities": activities,
        **kw,
    }
    r = api.req("POST", "/api/v1/optimizer/runs", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def by_obj(res, obj):
    return next(s for s in res["scenarios"] if obj in s["objectives"])


def test_feasible_run_lists_scenarios_that_respect_everything(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(api, acts(shift, oncall, svc), target=4_600_000)  # gap R$ 8.000
    assert res["feasible"] and res["outcome"] == "FEASIBLE" and res["gap_cents"] == 800_000
    assert 2 <= len(res["scenarios"]) <= 5
    less = by_obj(res, "LESS_HOURS")
    assert {u["label"]: u["units"] for u in less["units"]} == {
        "Plantão UPA": 3,
        "Consulta inicial": 10,
    }
    assert less["committed_minutes"] == 3 * 780 + 552  # 12h ativas + 1h de deslocamento
    assert less["net_gain_cents"] == 3 * 170_000 + 10 * 30_000 and less["gap_closed"]
    assert less["projected_after_cents"] == 3_800_000 + less["net_gain_cents"]
    assert [p["date"] for p in less["placements"]] == SHIFT_DAYS
    for sc in res["scenarios"]:
        assert sc["gap_closed"] and sc["net_gain_cents"] >= res["gap_cents"]
        assert sc["explanation"] and sc["labels"] and "risks" in sc
        for p in sc["placements"]:
            assert p["date"] in SHIFT_DAYS + ONCALL_DAYS and p["starts_at"].endswith("-03:00")
    assert any("MAX_MARGIN" in s["objectives"] for s in res["scenarios"])
    assert res["engine_version"] and res["assumptions"]


def test_prd_example_shortfall_is_shown_and_never_declared_met(make_user):
    """2 plantões + 10 consultas + 3 sobreavisos não fecham R$ 12.000: mostra o que falta."""
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(api, acts(shift, oncall, svc, shift_max=1), target=5_000_000)
    assert res["feasible"] is False and res["outcome"] == "INFEASIBLE" and res["scenarios"] == []
    assert res["max_achievable_gain_cents"] == 170_000 + 3 * 85_000 + 300_000
    assert res["shortfall_cents"] == 1_200_000 - res["max_achievable_gain_cents"]
    assert res["max_projected_cents"] == 3_800_000 + res["max_achievable_gain_cents"]
    assert "faltam" in res["message"]
    top = res["blocking_constraints"][0]
    assert top["closes_gap"] and top["labels"] and top["message"]
    assert "MAX_UNITS" in top["constraints"]


def test_zero_additional_hours_returns_no_work(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(
        api,
        acts(shift, oncall, svc),
        max_additional_active_minutes=0,
        max_additional_committed_minutes=0,
    )
    assert res["feasible"] is False and res["scenarios"] == []
    assert res["max_achievable_gain_cents"] == 0
    assert res["blocking_constraints"][0]["constraints"] == ["ACTIVE_LIMIT", "COMMITTED_LIMIT"]


def test_demand_limits_consults_not_just_guesses(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    only = [acts(shift, oncall, svc, consult_max=10, demand=3)[2]]
    res = run(api, only, target=3_800_000 + 150_000)  # 5 consultas
    assert res["feasible"] is False and res["max_achievable_gain_cents"] == 90_000
    assert any("DEMAND" in b["constraints"] for b in res["blocking_constraints"])
    unknown = run(api, [{**only[0], "demand_limit": None}], target=3_800_000 + 60_000)
    sc = unknown["scenarios"][0]
    assert any("dependem de captação" in r for r in sc["risks"])


def test_existing_event_removes_candidate_and_is_respected(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    ev = api.req("POST", "/api/v1/schedule/events", json={"type": "CONSULT_NEW",
        "starts_at": "2026-10-06T10:00:00-03:00", "ends_at": "2026-10-06T10:40:00-03:00"})  # fmt: skip
    assert ev.status_code == 201
    res = run(api, acts(shift, oncall, svc), target=4_600_000)
    assert res["info"]["candidates"]["dropped_busy"] >= 1
    for sc in res["scenarios"]:
        assert "2026-10-06" not in [p["date"] for p in sc["placements"] if "Plantão" in p["label"]]
    # sem o dia 6 sobram 2 plantões: o plano precisa de mais sobreaviso/consultas, ou é inviável
    less = by_obj(res, "LESS_HOURS") if res["feasible"] else None
    if less:
        assert sum(u["units"] for u in less["units"] if u["label"] == "Plantão UPA") <= 2


def test_confirmed_holiday_closes_a_candidate_day(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    api.req("POST", "/api/v1/holidays", json={"date": "2026-10-13", "name": "Feriado local"})
    res = run(api, acts(shift, oncall, svc), target=4_600_000)
    assert res["info"]["candidates"]["dropped_closed_day"] == 1
    for sc in res["scenarios"]:
        assert all(p["date"] != "2026-10-13" for p in sc["placements"])


def test_rest_after_shift_blocks_next_morning_on_call(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    api.req(
        "PUT", "/api/v1/time-constraints", json={"min_rest_minutes": 24 * 60}
    )  # 24h de descanso
    only = [
        {"source_type": "WORK", "source_id": shift["id"], "max_units": 1, "candidate_days": ["2026-10-06"]},
        {"source_type": "WORK", "source_id": oncall["id"], "max_units": 1, "candidate_days": ["2026-10-07"],
         "start_time": "08:00"},
    ]  # fmt: skip
    # 07–19 do dia 6 + 24h de descanso vai até 19h do dia 7: o sobreaviso das 8h do dia 7 não cabe
    res = run(api, only, target=3_800_000 + 255_000)
    assert res["feasible"] is False and res["max_achievable_gain_cents"] == 170_000
    assert any("CALENDAR" in b["constraints"] for b in res["blocking_constraints"])


def test_hours_headroom_comes_from_the_month_limits(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    api.req("PUT", "/api/v1/time-constraints", json={"month": M, "max_active_minutes": 1500})
    res = run(api, acts(shift, oncall, svc), target=4_600_000)
    assert res["info"]["headroom"]["active_minutes"] == 1500
    for sc in res["scenarios"]:
        assert sc["active_minutes"] <= 1500
    # horas já usadas na agenda consomem a folga
    api.req("POST", "/api/v1/schedule/events", json={"type": "TELEMEDICINE",
        "starts_at": "2026-10-08T08:00:00-03:00", "ends_at": "2026-10-08T18:00:00-03:00"})  # fmt: skip
    res2 = run(api, acts(shift, oncall, svc), target=4_600_000)
    assert res2["info"]["headroom"]["active_minutes"] == 1500 - 600


def test_objectives_subset_weights_and_validation(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    only = run(api, acts(shift, oncall, svc), target=4_600_000, objectives=["LESS_HOURS"])
    assert [s["objectives"] for s in only["scenarios"]] == [["LESS_HOURS"]]
    bal = run(api, acts(shift, oncall, svc), target=4_600_000, objectives=["BALANCED"],
              weights={"hours": 70, "change": 10, "risk": 10, "margin": 10})  # fmt: skip
    assert bal["scenarios"][0]["objectives"] == ["BALANCED"]
    for bad in ({"objectives": ["NOPE"]}, {"objectives": []}, {"weights": {"hours": 50}},
                {"weights": {"hours": 40, "change": 40, "risk": 40, "margin": -20}},
                {"month": "2026-13"}, {"target_cents": -1}, {"conservative_haircut_bps": 9000}):  # fmt: skip
        body = {"month": M, "target_cents": 1, "activities": acts(shift, oncall, svc), **bad}
        assert api.req("POST", "/api/v1/optimizer/runs", json=body).status_code == 422, bad
    dup = acts(shift, oncall, svc)[:1] * 2
    assert (
        api.req(
            "POST",
            "/api/v1/optimizer/runs",
            json={"month": M, "target_cents": 1, "activities": dup},
        ).status_code
        == 422
    )
    no_goal = {"month": M, "activities": acts(shift, oncall, svc)}
    assert api.req("POST", "/api/v1/optimizer/runs", json=no_goal).status_code == 422


def test_goal_already_met_means_stability_not_more_work(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(api, acts(shift, oncall, svc), target=3_000_000, baseline=3_800_000)
    assert (
        res["outcome"] == "ALREADY_MET"
        and res["scenarios"] == []
        and "estabilidade" in res["message"]
    )


def test_goal_id_uses_goal_and_dashboard_baseline(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    api.req("PUT", "/api/v1/income-plans", json={"month": M, "source_type": "WORK",
        "source_id": shift["id"], "units": 4, "certainty": "GUARANTEED"})  # fmt: skip
    g = api.req("POST", "/api/v1/goals", json={"month": M, "target_cents": 1_000_000}).json()
    res = run(api, acts(shift, oncall, svc), goal_id=g["id"], target=None, baseline=None)
    assert res["baseline_cents"] == 4 * 170_000 and res["gap_cents"] == 1_000_000 - 680_000
    other = api.req("POST", "/api/v1/goals", json={"month": "2026-11", "target_cents": 1}).json()
    bad = {"month": M, "goal_id": other["id"], "activities": acts(shift, oncall, svc)}
    assert api.req("POST", "/api/v1/optimizer/runs", json=bad).status_code == 422


def test_runs_are_deterministic(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    a = run(api, acts(shift, oncall, svc), target=4_600_000)
    b = run(api, acts(shift, oncall, svc), target=4_600_000)
    strip = lambda r: [{k: v for k, v in s.items() if k != "id"} for s in r["scenarios"]]  # noqa: E731
    assert strip(a) == strip(b) and a["run_id"] != b["run_id"]


def test_run_is_persisted_and_retrievable(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(api, acts(shift, oncall, svc), target=4_600_000)
    got = api.req("GET", f"/api/v1/optimizer/runs/{res['run_id']}").json()
    assert [s["id"] for s in got["scenarios"]] == [s["id"] for s in res["scenarios"]]
    assert got["outcome"] == "FEASIBLE" and got["engine_version"] == res["engine_version"]
    assert api.req("GET", "/api/v1/optimizer/runs").json()[0]["run_id"] == res["run_id"]


def test_compare_scenarios_side_by_side(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(api, acts(shift, oncall, svc), target=4_600_000)
    ids = [s["id"] for s in res["scenarios"][:2]]
    cmp_ = api.req("POST", "/api/v1/optimizer/compare", json={"scenario_ids": ids}).json()
    assert len(cmp_["scenarios"]) == 2 and "committed_minutes" in cmp_["scenarios"][0]
    assert (
        api.req("POST", "/api/v1/optimizer/compare", json={"scenario_ids": ids[:1]}).status_code
        == 422
    )


def test_saved_scenario_keeps_its_snapshot(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(api, acts(shift, oncall, svc), target=4_600_000)
    sid = res["scenarios"][0]["id"]
    sp = api.req("POST", f"/api/v1/optimizer/scenarios/{sid}/save").json()
    assert (
        sp["status"] == "SAVED"
        and sp["snapshot"]["net_gain_cents"] == res["scenarios"][0]["net_gain_cents"]
    )
    again = api.req("POST", f"/api/v1/optimizer/scenarios/{sid}/save").json()
    assert again["id"] == sp["id"]  # idempotente
    api.req("PATCH", f"/api/v1/work-sources/{shift['id']}", json={"income": {
        "gross_cents": 999_999, "unit_type": "PER_EVENT", "duration_minutes": 720}})  # fmt: skip
    after = api.req("GET", "/api/v1/optimizer/saved-plans").json()[0]
    assert after["snapshot"]["net_gain_cents"] == sp["snapshot"]["net_gain_cents"]


def test_apply_plans_without_registering_realized_and_is_idempotent(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    api.req("PUT", "/api/v1/income-plans", json={"month": M, "source_type": "WORK",
        "source_id": shift["id"], "units": 4, "certainty": "GUARANTEED"})  # fmt: skip
    before = api.req("GET", f"/api/v1/dashboard?month={M}").json()
    res = run(api, acts(shift, oncall, svc), target=1_300_000, baseline=None)
    sc = by_obj(res, "LESS_HOURS")
    ap = api.req("POST", f"/api/v1/optimizer/scenarios/{sc['id']}/apply", json={})
    assert ap.status_code == 200, ap.text
    after = api.req("GET", f"/api/v1/dashboard?month={M}").json()
    # o painel passa a incluir exatamente o ganho do cenário (cálculo exato = cálculo do solver)
    assert (
        after["projection"]["value_cents"]
        == before["projection"]["value_cents"] + sc["net_gain_cents"]
    )
    assert after["realized"] == before["realized"]  # nada vira realizado
    assert api.req("GET", f"/api/v1/transactions?month={M}").json() == []
    assert api.req("GET", "/api/v1/schedule/events?from=2026-10-01&to=2026-10-31").json() == []
    assert any("adicional do otimizador" in r["name"] for r in after["breakdown"])
    assert (
        api.req("POST", f"/api/v1/optimizer/scenarios/{sc['id']}/apply", json={}).status_code == 409
    )
    undo = api.req("POST", f"/api/v1/optimizer/saved-plans/{ap.json()['id']}/undo")
    assert undo.status_code == 200 and undo.json()["status"] == "UNDONE"
    restored = api.req("GET", f"/api/v1/dashboard?month={M}").json()
    assert restored["projection"]["value_cents"] == before["projection"]["value_cents"]
    assert (
        api.req("POST", f"/api/v1/optimizer/saved-plans/{ap.json()['id']}/undo").status_code == 409
    )


def test_apply_can_create_planned_events_only_when_asked(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(api, acts(shift, oncall, svc), target=4_600_000)
    sc = by_obj(res, "LESS_HOURS")
    ap = api.req(
        "POST", f"/api/v1/optimizer/scenarios/{sc['id']}/apply", json={"create_events": True}
    ).json()
    assert ap["events_created"] == 3
    evs = api.req("GET", "/api/v1/schedule/events?from=2026-10-01&to=2026-10-31").json()
    assert sorted(e["starts_at"][:10] for e in evs) == SHIFT_DAYS
    assert all(e["status"] == "PLANNED" and e["type"] == "SHIFT" for e in evs)
    # a agenda já ocupada tira esses dias da próxima rodada
    again = run(api, acts(shift, oncall, svc), target=4_600_000)
    assert again["info"]["candidates"]["dropped_busy"] >= 3
    api.req("POST", f"/api/v1/optimizer/saved-plans/{ap['id']}/undo")
    assert api.req("GET", "/api/v1/schedule/events?from=2026-10-01&to=2026-10-31").json() == []


def test_apply_refuses_when_agenda_changed(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    res = run(api, acts(shift, oncall, svc), target=4_600_000)
    sc = by_obj(res, "LESS_HOURS")
    api.req("POST", "/api/v1/schedule/events", json={"type": "BLOCK",
        "starts_at": "2026-10-06T09:00:00-03:00", "ends_at": "2026-10-06T10:00:00-03:00"})  # fmt: skip
    r = api.req(
        "POST", f"/api/v1/optimizer/scenarios/{sc['id']}/apply", json={"create_events": True}
    )
    assert r.status_code == 409 and "agenda mudou" in r.json()["detail"]
    assert api.req("GET", f"/api/v1/dashboard?month={M}").json()["breakdown"] == []  # nada mudou


def test_clinic_work_source_and_foreign_sources_rejected(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    shift, oncall, svc = setup(a)
    clinic = a.req(
        "POST", "/api/v1/work-sources", json={**SHIFT, "name": "Consultório", "work_type": "CLINIC"}
    ).json()
    body = {
        "month": M,
        "target_cents": 1,
        "activities": [{"source_type": "WORK", "source_id": clinic["id"], "max_units": 1}],
    }
    assert a.req("POST", "/api/v1/optimizer/runs", json=body).status_code == 422
    for act in acts(shift, oncall, svc):
        r = b.req(
            "POST",
            "/api/v1/optimizer/runs",
            json={"month": M, "target_cents": 1, "activities": [act]},
        )
        assert r.status_code == 422


def test_non_positive_activities_are_excluded_with_reason(make_user):
    api = make_user("a@x.com")
    shift, oncall, svc = setup(api)
    bad = api.req("POST", "/api/v1/work-sources", json={**SHIFT, "name": "Prejuízo", "income": {
        "gross_cents": 10_000, "unit_type": "PER_EVENT", "duration_minutes": 60, "variable_cost_cents": 20_000}}).json()  # fmt: skip
    res = run(api, acts(shift, oncall, svc) + [{"source_type": "WORK", "source_id": bad["id"], "max_units": 5}],
              target=4_600_000)  # fmt: skip
    assert res["excluded_activities"][0]["label"] == "Prejuízo"
    assert "não é positivo" in res["excluded_activities"][0]["reason"]
    assert all(u["label"] != "Prejuízo" for s in res["scenarios"] for u in s["units"])


# ---------- isolamento e papéis ----------


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    assert (
        owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role}).status_code
        == 201
    )


def test_cross_tenant_optimizer(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    shift, oncall, svc = setup(a)
    res = run(a, acts(shift, oncall, svc), target=4_600_000)
    sid = res["scenarios"][0]["id"]
    assert b.req("GET", f"/api/v1/optimizer/runs/{res['run_id']}").status_code == 404
    assert b.req("GET", "/api/v1/optimizer/runs").json() == []
    assert b.req("POST", f"/api/v1/optimizer/scenarios/{sid}/save").status_code == 404
    assert b.req("POST", f"/api/v1/optimizer/scenarios/{sid}/apply", json={}).status_code == 404
    other = run(b, [], expect=422) if False else None  # noqa: F841
    two = a.req(
        "POST",
        "/api/v1/optimizer/runs",
        json={
            "month": M,
            "target_cents": 1,
            "baseline_cents": 0,
            "activities": acts(shift, oncall, svc),
        },
    ).json()
    ids = [res["scenarios"][0]["id"], two["scenarios"][0]["id"]] if two["scenarios"] else [sid, sid]
    assert b.req("POST", "/api/v1/optimizer/compare", json={"scenario_ids": ids}).status_code == 404
    sp = a.req("POST", f"/api/v1/optimizer/scenarios/{sid}/save").json()
    assert b.req("POST", f"/api/v1/optimizer/saved-plans/{sp['id']}/undo").status_code == 404
    assert b.req("GET", "/api/v1/optimizer/runs", org_id=a.org_id).status_code == 404


def test_runs_are_personal_even_for_owner(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _member(owner, doc, "PHYSICIAN")
    w = doc.req("POST", "/api/v1/work-sources", org_id=owner.org_id, json=SHIFT).json()
    body = {"month": M, "target_cents": 3_000_000, "baseline_cents": 0,
            "activities": [{"source_type": "WORK", "source_id": w["id"], "max_units": 2,
                            "candidate_days": SHIFT_DAYS}]}  # fmt: skip
    r = doc.req("POST", "/api/v1/optimizer/runs", org_id=owner.org_id, json=body)
    assert r.status_code == 201
    assert owner.req("GET", f"/api/v1/optimizer/runs/{r.json()['run_id']}").status_code == 404
    assert owner.req("GET", "/api/v1/optimizer/runs").json() == []
    assert (
        owner.req("POST", "/api/v1/optimizer/runs", json=body).status_code == 422
    )  # trabalho é do médico


@pytest.mark.parametrize("role", ["ASSISTANT", "ACCOUNTANT", "MANAGER"])
def test_roles_without_personal_income_cannot_optimize(make_user, role):
    owner, other = make_user("o@x.com"), make_user("x@x.com")
    _member(owner, other, role)
    shift, oncall, svc = setup(owner)
    body = {"month": M, "target_cents": 1, "activities": acts(shift, oncall, svc)}
    assert (
        other.req("POST", "/api/v1/optimizer/runs", org_id=owner.org_id, json=body).status_code
        == 403
    )
    assert other.req("GET", "/api/v1/optimizer/runs", org_id=owner.org_id).status_code == 403
