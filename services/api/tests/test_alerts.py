"""Sprint 7: alertas por regra, notificações opt-in, anti-spam e privacidade."""

import pytest

from tests.test_goals import M, goal, setup_month, tx

TODAY = "2026-10-20"  # 65% do mês
NOW = "2026-10-20T10:00:00-03:00"
ALL = ["UPDATE_REALIZED", "GOAL_BEHIND_PACE", "GOAL_GAP", "SCHEDULE_PROBLEM", "SECRETARY_NO_COVERAGE",
       "EXPENSES_ABOVE_USUAL", "IDLE_CAPACITY", "REPORT_READY", "CONVERSION_DROP", "SUBSCRIPTION_AT_RISK"]  # fmt: skip


def alerts(api, as_of=TODAY, month=M, **kw):
    r = api.req("GET", f"/api/v1/alerts?month={month}&as_of={as_of}", **kw)
    assert r.status_code == 200, r.text
    return {a["kind"]: a for a in r.json()["alerts"]}


def settings(api, enabled=True, kinds=None, **kw):
    r = api.req("PUT", "/api/v1/notification-settings", json={"enabled": enabled,
        "kinds": ALL if kinds is None else kinds, **kw})  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def generate(api, now=NOW, month=M, **kw):
    r = api.req("POST", "/api/v1/notifications/generate", json={"month": month, "now": now}, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def inbox(api, **kw):
    return api.req("GET", "/api/v1/notifications", **kw).json()


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    assert (
        owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role}).status_code
        == 201
    )


def realize(api, w, units, amount, paid="2026-10-18"):
    r = api.req("POST", "/api/v1/realized", json={"source_type": "WORK", "source_id": w["id"], "month": M,
        "units": units, "amount_cents": amount, "paid_at": paid})  # fmt: skip
    assert r.status_code == 201, r.text


# ---------- alertas ao vivo ----------


def test_behind_pace_and_stale_realized(make_user):
    api = make_user("a@x.com")
    setup_month(api)
    a = alerts(api)
    assert "Nenhum lançamento realizado" in a["UPDATE_REALIZED"]["detail"]
    assert "0% do planejado com 65% do mês" in a["GOAL_BEHIND_PACE"]["detail"]
    assert (
        a["GOAL_BEHIND_PACE"]["severity"] == "warning"
        and a["GOAL_BEHIND_PACE"]["route"] == "/app/dashboard"
    )


def test_on_pace_has_no_pace_alerts(make_user):
    api = make_user("a@x.com")
    w, _ = setup_month(api, service_units=0)
    realize(api, w, 8, 1_600_000, paid="2026-10-19")
    a = alerts(api)
    assert "GOAL_BEHIND_PACE" not in a and "UPDATE_REALIZED" not in a


def test_goal_gap_alert_only_when_short(make_user):
    api = make_user("a@x.com")
    setup_month(api)
    goal(api, target=3_000_000)
    # projetado 27.100 (13.600 garantido + 13.500 provável) contra meta de 30.000
    detail = alerts(api)["GOAL_GAP"]["detail"]
    assert "Faltam R$ 2.900,00" in detail and "faltariam R$ 16.400,00" in detail
    g2 = make_user("b@x.com")
    setup_month(g2)
    goal(g2, target=100)
    assert "GOAL_GAP" not in alerts(g2)


def test_closed_or_future_months_do_not_nag_about_pace(make_user):
    api = make_user("a@x.com")
    setup_month(api)
    goal(api)
    assert not {"UPDATE_REALIZED", "GOAL_BEHIND_PACE", "GOAL_GAP"} & set(
        alerts(api, as_of="2026-11-05")
    )
    assert not {"UPDATE_REALIZED", "GOAL_BEHIND_PACE", "GOAL_GAP"} & set(
        alerts(api, as_of="2026-09-20")
    )


def test_schedule_problem_and_secretary_coverage(make_user):
    api = make_user("a@x.com")
    assert "SCHEDULE_PROBLEM" not in alerts(api)
    for h1, h2 in (("08:00", "08:40"), ("08:20", "09:00")):
        api.req("POST", "/api/v1/schedule/events", json={"type": "CONSULT_NEW", "allow_conflict": True,
            "starts_at": f"2026-10-06T{h1}:00-03:00", "ends_at": f"2026-10-06T{h2}:00-03:00"})  # fmt: skip
    a = alerts(api)
    assert "conflito de horário" in a["SCHEDULE_PROBLEM"]["detail"]
    api.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-10-26",
        "end_date": "2026-11-06", "coverage_factor_bps": 0})  # fmt: skip
    assert "sem cobertura" in alerts(api)["SECRETARY_NO_COVERAGE"]["detail"]
    covered = make_user("c@x.com")
    covered.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-10-26",
        "end_date": "2026-11-06", "coverage_factor_bps": 5000})  # fmt: skip
    assert "SECRETARY_NO_COVERAGE" not in alerts(covered)
    far = make_user("d@x.com")
    far.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-12-01",
        "end_date": "2026-12-10", "coverage_factor_bps": 0})  # fmt: skip
    assert "SECRETARY_NO_COVERAGE" not in alerts(far)  # fora da janela de 30 dias


def test_idle_capacity(make_user):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/availability/weekly", json={"weekdays": [0, 1, 2, 3, 4], "start_time": "08:00",
        "end_time": "12:00", "from_date": "2026-10-01", "to_date": "2026-10-31"})  # fmt: skip
    assert "da agenda restante" in alerts(api, as_of="2026-10-12")["IDLE_CAPACITY"]["detail"]
    for d in range(12, 31):  # agenda cheia no resto do mês
        api.req("POST", "/api/v1/schedule/events", json={"type": "BLOCK", "allow_conflict": True,
            "starts_at": f"2026-10-{d:02d}T08:00:00-03:00", "ends_at": f"2026-10-{d:02d}T12:00:00-03:00"})  # fmt: skip
    assert "IDLE_CAPACITY" not in alerts(api, as_of="2026-10-12")


def test_expenses_above_usual(make_user):
    api = make_user("a@x.com")
    for mo in ("2026-07", "2026-08", "2026-09"):
        tx(
            api,
            competence_month=mo,
            paid_at=f"{mo}-05",
            amount_cents=100_000,
            description=f"Aluguel {mo}",
        )
    tx(api, amount_cents=125_000, description="Aluguel out")
    assert "EXPENSES_ABOVE_USUAL" not in alerts(api)  # 25% acima: dentro do normal
    tx(api, amount_cents=100_000, description="Imprevisto", category="manutencao")
    a = alerts(api)["EXPENSES_ABOVE_USUAL"]
    assert "R$ 2.250,00" in a["detail"] and "R$ 1.000,00" in a["detail"]
    young = make_user("b@x.com")  # sem histórico, sem comparação
    tx(young, amount_cents=900_000)
    assert "EXPENSES_ABOVE_USUAL" not in alerts(young)


# ---------- notificações: opt-in, privacidade e anti-spam ----------


def test_alerts_are_opt_in_by_default(make_user):
    api = make_user("a@x.com")
    setup_month(api)
    s = api.req("GET", "/api/v1/notification-settings").json()
    assert s == {
        "enabled": False,
        "kinds": [],
        "quiet_start_hour": None,
        "quiet_end_hour": None,
        "channel": "IN_APP",
    }
    res = generate(api)
    assert res["created"] == [] and {x["reason"] for x in res["skipped"]} == {"disabled"}
    assert inbox(api) == {"unread_count": 0, "items": []}
    settings(api, enabled=True, kinds=[])
    assert {x["reason"] for x in generate(api)["skipped"]} == {"kind_off"}


def test_notifications_are_generic_and_never_carry_amounts(make_user):
    api = make_user("a@x.com")
    setup_month(api)
    goal(api)
    settings(api)
    res = generate(api)
    assert {c["kind"] for c in res["created"]} >= {
        "GOAL_GAP",
        "GOAL_BEHIND_PACE",
        "UPDATE_REALIZED",
    }
    box = inbox(api)
    assert box["unread_count"] == len(res["created"]) and len(box["items"]) == len(res["created"])
    for n in box["items"]:
        assert n["body"] == "Abra o FinanceMed AI para ver os detalhes."
        assert "R$" not in n["title"] + n["body"] and not any(
            ch.isdigit() for ch in n["title"] + n["body"]
        )
        assert n["route"].startswith("/app/") and not n["read"] and n["ref"] == M
    # o detalhe com valores existe, mas só dentro do app autenticado
    assert "R$" in alerts(api)["GOAL_GAP"]["detail"]


def test_generate_is_idempotent_and_respects_frequency(make_user):
    api = make_user("a@x.com")
    setup_month(api)
    goal(api)
    settings(api)
    first = generate(api)
    again = generate(api, now="2026-10-20T11:00:00-03:00")
    assert again["created"] == [] and {s["reason"] for s in again["skipped"]} <= {
        "frequency",
        "daily_limit",
    }
    assert len(inbox(api)["items"]) == len(first["created"])
    later = generate(api, now="2026-10-29T10:00:00-03:00")  # passou o intervalo de 7 dias
    assert {c["kind"] for c in later["created"]} & {"GOAL_GAP", "UPDATE_REALIZED"} or later[
        "skipped"
    ]


def test_daily_limit_caps_notifications(make_user):
    api = make_user("a@x.com")
    setup_month(api)
    goal(api)
    api.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-10-26",
        "end_date": "2026-11-06", "coverage_factor_bps": 0})  # fmt: skip
    api.req("POST", "/api/v1/availability/weekly", json={"weekdays": [0, 1, 2, 3, 4], "start_time": "08:00",
        "end_time": "12:00", "from_date": "2026-10-01", "to_date": "2026-10-31"})  # fmt: skip
    settings(api)
    assert len(alerts(api)) >= 5
    res = generate(api)
    assert len(res["created"]) == 3
    assert "daily_limit" in {s["reason"] for s in res["skipped"]}
    assert len(inbox(api)["items"]) == 3


@pytest.mark.parametrize(
    "now,quiet",
    [
        ("2026-10-20T23:30:00-03:00", True),
        ("2026-10-20T03:00:00-03:00", True),
        ("2026-10-20T12:00:00-03:00", False),
    ],
)
def test_quiet_hours_wrapping_midnight(make_user, now, quiet):
    api = make_user("a@x.com")
    setup_month(api)
    settings(api, quiet_start_hour=22, quiet_end_hour=7)
    res = generate(api, now=now)
    if quiet:
        assert res["created"] == [] and {s["reason"] for s in res["skipped"]} == {"quiet_hours"}
    else:
        assert res["created"]


def test_read_and_read_all(make_user):
    api = make_user("a@x.com")
    setup_month(api)
    goal(api)
    settings(api)
    generate(api)
    box = inbox(api)
    first = box["items"][0]["id"]
    assert api.req("POST", f"/api/v1/notifications/{first}/read").status_code == 204
    assert inbox(api)["unread_count"] == box["unread_count"] - 1
    assert (
        len(api.req("GET", "/api/v1/notifications?unread_only=true").json()["items"])
        == box["unread_count"] - 1
    )
    assert (
        api.req("POST", "/api/v1/notifications/read-all").json()["marked"]
        == box["unread_count"] - 1
    )
    assert inbox(api)["unread_count"] == 0 and all(n["read"] for n in inbox(api)["items"])


def test_report_ready_notification_only_when_opted_in(make_user):
    api = make_user("a@x.com")
    r = {"template": "MONTHLY_RESULT", "period": M, "format": "PDF"}
    api.req("POST", "/api/v1/reports/generate", json=r)
    assert inbox(api)["items"] == []
    settings(api, kinds=["REPORT_READY"])
    api.req("POST", "/api/v1/reports/generate", json=r)
    items = inbox(api)["items"]
    assert [i["kind"] for i in items] == ["REPORT_READY"] and items[0]["route"] == "/app/reports"
    api.req("POST", "/api/v1/reports/generate", json=r)  # no mesmo dia: sem repetir
    assert len(inbox(api)["items"]) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"kinds": ["NOPE"]},
        {"quiet_start_hour": 22},
        {"quiet_end_hour": 7},
        {"quiet_start_hour": 24, "quiet_end_hour": 7},
    ],
)
def test_invalid_settings(make_user, bad):
    api = make_user("a@x.com")
    r = api.req("PUT", "/api/v1/notification-settings", json={"enabled": True, "kinds": [], **bad})
    assert r.status_code == 422


def test_settings_are_per_user_and_notifications_are_private(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _member(owner, doc, "PHYSICIAN")
    setup_month(owner)
    goal(owner)
    settings(owner)
    generate(owner)
    assert (
        doc.req("GET", "/api/v1/notification-settings", org_id=owner.org_id).json()["enabled"]
        is False
    )
    assert doc.req("GET", "/api/v1/notifications", org_id=owner.org_id).json()["items"] == []
    nid = inbox(owner)["items"][0]["id"]
    assert (
        doc.req("POST", f"/api/v1/notifications/{nid}/read", org_id=owner.org_id).status_code == 404
    )
    other = make_user("x@x.com")
    assert other.req("POST", f"/api/v1/notifications/{nid}/read").status_code == 404
    assert other.req("GET", "/api/v1/notifications", org_id=owner.org_id).status_code == 404


def test_assistant_only_sees_schedule_alerts(make_user):
    owner, sec_ = make_user("o@x.com"), make_user("s@x.com")
    _member(owner, sec_, "ASSISTANT")
    setup_month(owner)
    goal(owner)
    owner.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-10-26",
        "end_date": "2026-11-06", "coverage_factor_bps": 0})  # fmt: skip
    kinds = set(alerts(sec_, org_id=owner.org_id))
    assert (
        kinds <= {"SCHEDULE_PROBLEM", "SECRETARY_NO_COVERAGE", "IDLE_CAPACITY"}
        and "SECRETARY_NO_COVERAGE" in kinds
    )
    assert not {"GOAL_GAP", "GOAL_BEHIND_PACE", "UPDATE_REALIZED", "EXPENSES_ABOVE_USUAL"} & kinds


def test_kind_catalog(make_user):
    api = make_user("a@x.com")
    cat = api.req("GET", "/api/v1/notification-kinds").json()
    assert {c["kind"] for c in cat} == set(ALL) and all(c["min_interval_days"] >= 1 for c in cat)
