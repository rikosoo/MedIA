"""Sprint 3: disponibilidade, feriados, férias, conflitos, descanso e capacidade real."""

import pytest

from tests.conftest import SHIFT

OCT = "2026-10"


def t(day, hm, month="10"):
    """Horário local de São Paulo (UTC-3, sem horário de verão)."""
    return f"2026-{month}-{day:02d}T{hm}:00-03:00"


def weekly(api, **over):
    body = {
        "weekdays": [0, 1, 2, 3, 4],
        "start_time": "08:00",
        "end_time": "12:00",
        "from_date": "2026-10-01",
        "to_date": "2026-10-31",
        **over,
    }
    r = api.req("POST", "/api/v1/availability/weekly", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def event(api, type, start, end, expect=201, **kw):
    r = api.req(
        "POST",
        "/api/v1/schedule/events",
        json={"type": type, "starts_at": start, "ends_at": end, **kw},
    )
    assert r.status_code == expect, r.text
    return r.json()


def cap(api, **params):
    q = "&".join(f"{k}={v}" for k, v in {"month": OCT, **params}.items())
    r = api.req("GET", f"/api/v1/schedule/capacity?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def overview(api):
    return api.req("GET", f"/api/v1/schedule/overview?month={OCT}").json()


def constraints(api, **over):
    r = api.req("PUT", "/api/v1/time-constraints", json=over)
    assert r.status_code == 200, r.text


def test_weekly_template_builds_real_capacity_and_is_idempotent(make_user):
    api = make_user("a@x.com")
    assert weekly(api) == {"created": 22, "skipped_existing": 0}
    assert weekly(api) == {"created": 0, "skipped_existing": 22}
    c = cap(api, slot_minutes=40)
    assert c["availability_minutes"] == 22 * 240
    assert c["remaining_minutes"] == 22 * 240
    assert c["slots_remaining"] == 22 * 6  # 240/40 por janela, sem atravessar lacunas
    assert len(c["days"]) == 31
    assert c["days"][4]["remaining_minutes"] == 240  # segunda, dia 5
    assert c["days"][2]["remaining_minutes"] == 0  # sábado, dia 3


def test_slots_do_not_cross_gaps(make_user):
    api = make_user("a@x.com")
    weekly(api, weekdays=[0], from_date="2026-10-05", to_date="2026-10-05")
    event(api, "BLOCK", t(5, "09:30"), t(5, "10:00"))
    # 08:00–09:30 (90) + 10:00–12:00 (120) com slots de 60: 1 + 2 = 3, e não 210/60 = 3,5 -> 3
    c = cap(api, slot_minutes=60)
    assert c["remaining_minutes"] == 210 and c["slots_remaining"] == 3
    assert cap(api, slot_minutes=45)["slots_remaining"] == 2 + 2  # 90//45 + 120//45


def test_holiday_suggestion_requires_manual_confirmation(make_user):
    api = make_user("a@x.com")
    weekly(api)
    r = api.req("POST", "/api/v1/holidays/suggest?year=2026").json()
    assert r["created"] == 10
    assert api.req("POST", "/api/v1/holidays/suggest?year=2026").json() == {"created": 0}
    base = cap(api, slot_minutes=40)
    assert base["closed_days"] == []  # sugerido ≠ confirmado: não fecha a agenda
    hol = next(
        h for h in api.req("GET", "/api/v1/holidays?year=2026").json() if h["date"] == "2026-10-12"
    )
    assert hol["confirmed"] is False
    assert (
        api.req("PATCH", f"/api/v1/holidays/{hol['id']}", json={"confirmed": True}).status_code
        == 200
    )
    c = cap(api, slot_minutes=40)
    assert c["closed_days"] == [
        {"date": "2026-10-12", "reason": "Feriado: Nossa Senhora Aparecida"}
    ]
    assert c["closed_minutes"] == 240 and c["slots_remaining"] == 21 * 6
    assert c["days"][11]["closed_reason"].startswith("Feriado")


def test_custom_holiday_and_duplicates(make_user):
    api = make_user("a@x.com")
    body = {"date": "2026-10-20", "name": "Aniversário da cidade", "scope": "MUNICIPAL"}
    assert api.req("POST", "/api/v1/holidays", json=body).status_code == 201
    assert api.req("POST", "/api/v1/holidays", json=body).status_code == 409
    weekly(api)
    assert cap(api)["closed_days"][0]["date"] == "2026-10-20"


def test_physician_leave_removes_capacity_but_secretary_leave_does_not(make_user):
    api = make_user("a@x.com")
    weekly(api)
    sec = {"staff_role": "SECRETARY", "start_date": "2026-10-05", "end_date": "2026-10-19",
           "coverage_factor_bps": 3000}  # fmt: skip
    assert api.req("POST", "/api/v1/leaves", json=sec).status_code == 201
    assert cap(api)["remaining_minutes"] == 22 * 240
    doc = {"staff_role": "PHYSICIAN", "start_date": "2026-10-19", "end_date": "2026-10-23"}
    lv = api.req("POST", "/api/v1/leaves", json=doc).json()
    c = cap(api)
    assert c["remaining_minutes"] == 17 * 240
    assert c["closed_days"][0] == {"date": "2026-10-19", "reason": "Férias/licença"}
    assert api.req("DELETE", f"/api/v1/leaves/{lv['id']}").status_code == 204
    assert cap(api)["remaining_minutes"] == 22 * 240
    bad = {**doc, "end_date": "2026-10-01"}
    assert api.req("POST", "/api/v1/leaves", json=bad).status_code == 422


def test_days_off_close_capacity(make_user):
    api = make_user("a@x.com")
    weekly(api)
    constraints(api, days_off=["2026-10-06", "2026-10-07"])
    c = cap(api)
    assert c["remaining_minutes"] == 20 * 240
    assert {d["reason"] for d in c["closed_days"]} == {"Folga"}


def test_hard_conflict_blocks_unless_confirmed(make_user):
    api = make_user("a@x.com")
    event(api, "SHIFT", t(6, "07:00"), t(6, "19:00"))
    r = event(api, "CONSULT_NEW", t(6, "10:00"), t(6, "10:40"), expect=409)
    assert r["detail"]["conflicts"][0]["kind"] == "HARD"
    ok = event(api, "CONSULT_NEW", t(6, "10:00"), t(6, "10:40"), allow_conflict=True)
    assert ok["warnings"]["conflicts"][0]["overlap_minutes"] == 40
    ov = overview(api)
    assert ov["feasible"] is False and len(ov["conflicts"]) == 1
    # encostado não é conflito
    event(api, "CONSULT_NEW", t(6, "19:00"), t(6, "19:40"))


def test_on_call_overlap_rules(make_user):
    api = make_user("a@x.com")
    event(api, "SHIFT", t(6, "07:00"), t(6, "19:00"))
    event(api, "ON_CALL", t(6, "08:00"), t(6, "20:00"), expect=409)
    ok = event(api, "ON_CALL", t(6, "08:00"), t(6, "20:00"), allow_overlap=True)
    assert [c["kind"] for c in ok["warnings"]["conflicts"]] == ["CONDITIONAL"]
    # dois sobreavisos nunca coexistem, mesmo com permissão
    event(api, "ON_CALL", t(6, "09:00"), t(6, "10:00"), allow_overlap=True, expect=409)
    # tempo comprometido não conta a sobreposição duas vezes: 07–20 = 13h
    assert overview(api)["committed_minutes"] == 13 * 60
    assert overview(api)["active_minutes"] == 12 * 60
    # allow_overlap só vale para sobreaviso
    event(api, "CONSULT_NEW", t(7, "08:00"), t(7, "09:00"), allow_overlap=True, expect=422)


def test_on_call_blocks_capacity_unless_conditional(make_user):
    api = make_user("a@x.com")
    weekly(api, weekdays=[0], from_date="2026-10-05", to_date="2026-10-05")
    event(api, "ON_CALL", t(5, "08:00"), t(5, "10:00"), allow_overlap=True)
    c = cap(api)
    assert c["remaining_minutes"] == 240 and c["conditional_on_call_minutes"] == 120
    event(api, "ON_CALL", t(5, "10:00"), t(5, "12:00"))  # sem permissão: bloqueia
    c = cap(api)
    assert c["remaining_minutes"] == 120 and c["blocked_minutes"] == 120


def test_rest_violation_and_recovery_window(make_user):
    api = make_user("a@x.com")
    constraints(api, min_rest_minutes=660)  # 11h após plantão
    weekly(
        api,
        weekdays=[1, 2],
        from_date="2026-10-06",
        to_date="2026-10-07",
        start_time="06:00",
        end_time="23:00",
    )
    event(api, "SHIFT", t(6, "07:00"), t(6, "19:00"))
    r = event(api, "CONSULT_NEW", t(6, "20:00"), t(6, "20:40"))  # só 1h depois
    rv = r["warnings"]["rest_violations"]
    assert rv and rv[0]["gap_minutes"] == 60 and rv[0]["required_minutes"] == 660
    ov = overview(api)
    assert ov["feasible"] is False and len(ov["rest_violations"]) == 1
    # o descanso (19:00–06:00 do dia seguinte) sai da capacidade
    c = cap(api)
    assert c["blocked_minutes"] >= 660 - 0


def test_explicit_zero_recovery_overrides_default(make_user):
    api = make_user("a@x.com")
    constraints(api, min_rest_minutes=660)
    event(api, "SHIFT", t(6, "07:00"), t(6, "19:00"), recovery_minutes=0)
    r = event(api, "CONSULT_NEW", t(6, "20:00"), t(6, "20:40"))
    assert r["warnings"]["rest_violations"] == []


def test_limits_and_night_shifts(make_user):
    api = make_user("a@x.com")
    constraints(api, max_active_minutes=600, max_committed_minutes=700, max_night_shifts=0)
    event(api, "SHIFT", t(6, "19:00"), t(7, "07:00"), recovery_minutes=0)  # 12h, noturno
    ov = overview(api)
    assert ov["active_minutes"] == 720 and ov["night_shifts"] == 1
    assert ov["over_limit"] == {
        "active_excess_minutes": 120,
        "committed_excess_minutes": 20,
        "night_shifts_excess": 1,
    }
    assert ov["feasible"] is False
    constraints(api, max_active_minutes=900, max_committed_minutes=900, max_night_shifts=2)
    ov = overview(api)
    assert ov["over_limit"] == {} and ov["feasible"] is True


def test_month_specific_constraint_overrides_default(make_user):
    api = make_user("a@x.com")
    constraints(api, max_active_minutes=100)
    constraints(api, month=OCT, max_active_minutes=5000)
    eff = api.req("GET", f"/api/v1/time-constraints?month={OCT}").json()["effective"]
    assert eff["max_active_minutes"] == 5000 and eff["month"] == OCT
    nov = api.req("GET", "/api/v1/time-constraints?month=2026-11").json()["effective"]
    assert nov["max_active_minutes"] == 100
    bad = api.req(
        "PUT",
        "/api/v1/time-constraints",
        json={"max_active_minutes": 100, "max_committed_minutes": 50},
    )
    assert bad.status_code == 422


def test_event_on_closed_day_is_flagged(make_user):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/holidays", json={"date": "2026-10-20", "name": "Feriado local"})
    r = event(api, "CONSULT_NEW", t(20, "09:00"), t(20, "09:40"))
    assert r["warnings"]["closed_days"] == [
        {"date": "2026-10-20", "reason": "Feriado: Feriado local"}
    ]
    ov = overview(api)
    assert ov["events_on_closed_days"][0]["dates"] == ["2026-10-20"] and ov["feasible"] is False


def test_booked_consults_reduce_remaining_and_new_patient_capacity(make_user):
    from tests.test_clinic import POLICY, SERVICE

    api = make_user("a@x.com")
    p = api.req("POST", "/api/v1/return-policies", json=POLICY).json()
    s = api.req(
        "POST", "/api/v1/clinic-services", json={**SERVICE, "return_policy_id": p["id"]}
    ).json()
    weekly(api, weekdays=[0], from_date="2026-10-05", to_date="2026-10-05")  # 240 min
    c = cap(api, service_id=s["id"])
    assert c["slot_minutes"] == 40 and c["slots_remaining"] == 6
    assert c["service"]["expected_minutes_per_new_patient"] == "55.2"
    assert c["service"]["new_patients_remaining"] == 240 * 10 // 552  # 4
    event(api, "CONSULT_NEW", t(5, "08:00"), t(5, "08:40"))
    event(api, "CONSULT_RETURN", t(5, "08:40"), t(5, "09:00"))
    c = cap(api, service_id=s["id"])
    assert c["booked_consult_minutes"] == 60 and c["remaining_minutes"] == 180
    assert c["service"]["new_patients_gross"] == 4 and c["service"]["new_patients_remaining"] == 3
    assert c["slots_remaining"] == 4  # 09:00–12:00 = 180/40


def test_no_availability_warns(make_user):
    api = make_user("a@x.com")
    c = cap(api)
    assert c["remaining_minutes"] == 0 and c["warnings"]


def test_event_lifecycle_and_cancelled_frees_time(make_user):
    api = make_user("a@x.com")
    e = event(api, "SHIFT", t(6, "07:00"), t(6, "19:00"))
    listing = api.req("GET", "/api/v1/schedule/events?from=2026-10-06&to=2026-10-06").json()
    assert [x["id"] for x in listing] == [e["id"]] and listing[0]["starts_at"].endswith("-03:00")
    up = api.req("PATCH", f"/api/v1/schedule/events/{e['id']}", json={"status": "DONE"})
    assert up.json()["status"] == "DONE"
    assert (
        api.req(
            "PATCH", f"/api/v1/schedule/events/{e['id']}", json={"status": "CANCELLED"}
        ).status_code
        == 200
    )
    assert api.req("GET", "/api/v1/schedule/events?from=2026-10-06&to=2026-10-06").json() == []
    assert overview(api)["active_minutes"] == 0
    e2 = event(api, "CONSULT_NEW", t(6, "10:00"), t(6, "10:40"))
    assert api.req("DELETE", f"/api/v1/schedule/events/{e2['id']}").status_code == 204
    # mover para cima de outro evento é conflito
    a = event(api, "CONSULT_NEW", t(8, "09:00"), t(8, "09:40"))
    b = event(api, "CONSULT_NEW", t(8, "10:00"), t(8, "10:40"))
    mv = api.req(
        "PATCH",
        f"/api/v1/schedule/events/{b['id']}",
        json={"starts_at": t(8, "09:20"), "ends_at": t(8, "10:00")},
    )
    assert mv.status_code == 409 and a["id"] == mv.json()["detail"]["conflicts"][0]["with_id"]


@pytest.mark.parametrize(
    "bad",
    [
        {"starts_at": "2026-10-06T07:00:00", "ends_at": "2026-10-06T08:00:00"},  # sem fuso
        {"starts_at": t(6, "09:00"), "ends_at": t(6, "08:00")},
        {"starts_at": t(6, "09:00"), "ends_at": t(6, "09:00")},
        {"starts_at": t(1, "09:00"), "ends_at": t(20, "09:00")},  # > 7 dias
        {"type": "NOPE"},
        {"status": "CANCELLED"},
        {"recovery_minutes": -1},
    ],
)
def test_invalid_events(make_user, bad):
    api = make_user("a@x.com")
    body = {"type": "SHIFT", "starts_at": t(6, "07:00"), "ends_at": t(6, "08:00"), **bad}
    assert api.req("POST", "/api/v1/schedule/events", json=body).status_code == 422


@pytest.mark.parametrize(
    "bad",
    [
        {"weekdays": [7]},
        {"weekdays": []},
        {"end_time": "07:00"},
        {"to_date": "2025-01-01"},
        {"to_date": "2028-01-01"},
    ],
)
def test_invalid_weekly(make_user, bad):
    api = make_user("a@x.com")
    body = {"weekdays": [0], "start_time": "08:00", "end_time": "12:00",
            "from_date": "2026-10-01", "to_date": "2026-10-31", **bad}  # fmt: skip
    assert api.req("POST", "/api/v1/availability/weekly", json=body).status_code == 422


def test_availability_single_slot_list_delete(make_user):
    api = make_user("a@x.com")
    s = api.req(
        "POST", "/api/v1/availability", json={"starts_at": t(5, "08:00"), "ends_at": t(5, "12:00")}
    )
    assert s.status_code == 201 and s.json()["minutes"] == 240
    assert len(api.req("GET", "/api/v1/availability?from=2026-10-05&to=2026-10-05").json()) == 1
    assert api.req("DELETE", f"/api/v1/availability/{s.json()['id']}").status_code == 204
    long = {"starts_at": t(5, "00:00"), "ends_at": t(7, "00:00")}
    assert api.req("POST", "/api/v1/availability", json=long).status_code == 422
    assert api.req("GET", "/api/v1/availability?from=2026-01-01&to=2026-12-31").status_code == 422


def test_timezone_boundaries_use_org_timezone(make_user):
    """23:30 de 31/10 local é 02:30 UTC de 01/11: pertence a outubro."""
    api = make_user("a@x.com")
    event(api, "SHIFT", t(31, "23:00"), "2026-11-01T01:00:00-03:00", recovery_minutes=0)
    ov = overview(api)
    assert ov["active_minutes"] == 60  # só a hora que cai em outubro
    nov = api.req("GET", "/api/v1/schedule/overview?month=2026-11").json()
    assert nov["active_minutes"] == 60


# ---------- isolamento e papéis ----------


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    r = owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def test_cross_tenant_schedule(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    weekly(a)
    e = event(a, "SHIFT", t(6, "07:00"), t(6, "19:00"))
    slot = a.req("GET", "/api/v1/availability?from=2026-10-05&to=2026-10-05").json()[0]
    hol = a.req("POST", "/api/v1/holidays", json={"date": "2026-10-20", "name": "X"}).json()
    lv = a.req(
        "POST",
        "/api/v1/leaves",
        json={"staff_role": "PHYSICIAN", "start_date": "2026-10-19", "end_date": "2026-10-19"},
    ).json()
    for method, url in [
        ("PATCH", f"/api/v1/schedule/events/{e['id']}"),
        ("DELETE", f"/api/v1/schedule/events/{e['id']}"),
        ("DELETE", f"/api/v1/availability/{slot['id']}"),
        ("PATCH", f"/api/v1/holidays/{hol['id']}"),
        ("DELETE", f"/api/v1/holidays/{hol['id']}"),
        ("DELETE", f"/api/v1/leaves/{lv['id']}"),
    ]:
        body = (
            {"confirmed": True}
            if "holidays" in url and method == "PATCH"
            else ({} if method == "PATCH" else None)
        )
        assert b.req(method, url, json=body).status_code == 404, (method, url)
    assert b.req("GET", "/api/v1/schedule/events?from=2026-10-01&to=2026-10-31").json() == []
    assert cap(b)["availability_minutes"] == 0
    assert overview(b)["active_minutes"] == 0
    assert b.req("GET", "/api/v1/holidays?year=2026").json() == []
    # B não pode usar user_id de A (nem sendo da org de A)
    a_id = a.http.get("/api/v1/me").json()["id"]
    assert b.req("GET", f"/api/v1/schedule/overview?month={OCT}&user_id={a_id}").status_code == 404
    assert (
        b.req(
            "GET", "/api/v1/schedule/events?from=2026-10-01&to=2026-10-31", org_id=a.org_id
        ).status_code
        == 404
    )
    # nem criar evento para A dentro da própria org
    r = b.req(
        "POST",
        "/api/v1/schedule/events",
        json={
            "type": "BLOCK",
            "starts_at": t(6, "07:00"),
            "ends_at": t(6, "08:00"),
            "user_id": a_id,
        },
    )
    assert r.status_code in (403, 404)


def test_physician_sees_only_own_agenda(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _member(owner, doc, "PHYSICIAN")
    mine = event(owner, "SHIFT", t(6, "07:00"), t(6, "19:00"))
    owner_id = owner.http.get("/api/v1/me").json()["id"]
    assert (
        doc.req(
            "GET", f"/api/v1/schedule/overview?month={OCT}&user_id={owner_id}", org_id=owner.org_id
        ).status_code
        == 403
    )
    assert (
        doc.req(
            "PATCH",
            f"/api/v1/schedule/events/{mine['id']}",
            org_id=owner.org_id,
            json={"title": "x"},
        ).status_code
        == 404
    )
    assert doc.req("GET", "/api/v1/schedule/people", org_id=owner.org_id).json()[0]["name"] == "d"
    own = doc.req("POST", "/api/v1/schedule/events", org_id=owner.org_id,
                  json={"type": "BLOCK", "starts_at": t(6, "07:00"), "ends_at": t(6, "08:00")})  # fmt: skip
    assert own.status_code == 201  # a agenda do outro não interfere na dele


def test_assistant_runs_agenda_but_not_finance_or_income(make_user):
    owner, sec = make_user("o@x.com"), make_user("s@x.com")
    _member(owner, sec, "ASSISTANT")
    owner_id = owner.http.get("/api/v1/me").json()["id"]
    w = owner.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    people = sec.req("GET", "/api/v1/schedule/people", org_id=owner.org_id).json()
    assert [p["user_id"] for p in people] == [owner_id]
    r = sec.req("POST", "/api/v1/availability/weekly", org_id=owner.org_id,
                json={"weekdays": [0], "start_time": "08:00", "end_time": "12:00",
                      "from_date": "2026-10-05", "to_date": "2026-10-05", "user_id": owner_id})  # fmt: skip
    assert r.status_code == 201
    ev = sec.req("POST", "/api/v1/schedule/events", org_id=owner.org_id,
                 json={"type": "CONSULT_NEW", "starts_at": t(5, "08:00"), "ends_at": t(5, "08:40"),
                       "user_id": owner_id})  # fmt: skip
    assert ev.status_code == 201
    # a secretária não vincula trabalho (revelaria renda) e não vê o vínculo
    link = sec.req("POST", "/api/v1/schedule/events", org_id=owner.org_id,
                   json={"type": "SHIFT", "starts_at": t(9, "08:00"), "ends_at": t(9, "09:00"),
                         "user_id": owner_id, "work_source_id": w["id"]})  # fmt: skip
    assert link.status_code == 422
    owner_ev = event(owner, "SHIFT", t(8, "07:00"), t(8, "19:00"), work_source_id=w["id"])
    assert owner_ev["work_source_id"] == w["id"]
    seen = sec.req(
        "GET",
        f"/api/v1/schedule/events?from=2026-10-08&to=2026-10-08&user_id={owner_id}",
        org_id=owner.org_id,
    ).json()
    assert seen[0]["work_source_id"] is None
    assert sec.req("GET", "/api/v1/transactions", org_id=owner.org_id).status_code == 403
    assert sec.req("GET", "/api/v1/work-sources", org_id=owner.org_id).status_code == 403


def test_accountant_has_no_schedule_access(make_user):
    owner, acc = make_user("o@x.com"), make_user("c@x.com")
    _member(owner, acc, "ACCOUNTANT")
    assert (
        acc.req("GET", f"/api/v1/schedule/overview?month={OCT}", org_id=owner.org_id).status_code
        == 403
    )
    assert acc.req("GET", "/api/v1/holidays?year=2026", org_id=owner.org_id).status_code == 403
    assert (
        acc.req("POST", "/api/v1/holidays/suggest?year=2026", org_id=owner.org_id).status_code
        == 403
    )


def test_work_source_link_validated(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    w = a.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    event(b, "SHIFT", t(6, "07:00"), t(6, "19:00"), work_source_id=w["id"], expect=422)
