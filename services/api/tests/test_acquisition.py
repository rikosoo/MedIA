"""Sprint 8: funil de captação, gargalo, atribuição transparente e cobertura da secretária."""

import pytest

from tests.test_clinic import POLICY, SERVICE

M = "2026-10"


def channel(api, name="Google", typ="GOOGLE", expect=201, **kw):
    r = api.req(
        "POST", "/api/v1/acquisition/channels", json={"name": name, "channel_type": typ, **kw}
    )
    assert r.status_code == expect, r.text
    return r.json()


def metric(
    api, ch, month=M, leads=100, responded=90, bookings=40, attended=30, spend=60_000, expect=200
):
    r = api.req("PUT", "/api/v1/acquisition/metrics", json={"month": month, "channel_id": ch["id"],
        "leads_count": leads, "responded_in_time_count": responded, "bookings_count": bookings,
        "attended_count": attended, "spend_cents": spend})  # fmt: skip
    assert r.status_code == expect, r.text
    return r.json()


def funnel(api, month=M, **kw):
    r = api.req("GET", f"/api/v1/acquisition/funnel?month={month}", **kw)
    assert r.status_code == 200, r.text
    return r.json()


def service(api):
    p = api.req("POST", "/api/v1/return-policies", json=POLICY).json()
    return api.req(
        "POST", "/api/v1/clinic-services", json={**SERVICE, "return_policy_id": p["id"]}
    ).json()


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    assert (
        owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role}).status_code
        == 201
    )


def test_channels_crud_and_deactivation(make_user):
    api = make_user("a@x.com")
    g = channel(api)
    assert channel(api, expect=409)["detail"].startswith("Já existe")
    ig = channel(api, "Instagram", "INSTAGRAM")
    assert [c["name"] for c in api.req("GET", "/api/v1/acquisition/channels").json()] == [
        "Google",
        "Instagram",
    ]
    r = api.req("PATCH", f"/api/v1/acquisition/channels/{ig['id']}", json={"active": False})
    assert r.json()["active"] is False
    assert [c["name"] for c in api.req("GET", "/api/v1/acquisition/channels").json()] == ["Google"]
    assert len(api.req("GET", "/api/v1/acquisition/channels?include_inactive=true").json()) == 2
    assert (
        api.req(
            "PATCH", f"/api/v1/acquisition/channels/{ig['id']}", json={"name": "Google"}
        ).status_code
        == 409
    )
    assert (
        api.req(
            "PATCH", f"/api/v1/acquisition/channels/{g['id']}", json={"channel_type": "NOPE"}
        ).status_code
        == 422
    )
    channel(api, "X", "TIKTOK", expect=422)


def test_metric_upsert_validation_and_rates(make_user):
    api = make_user("a@x.com")
    g = channel(api)
    out = metric(api, g)
    assert (
        out["lead_to_booking_bps"],
        out["booking_to_attendance_bps"],
        out["response_rate_bps"],
    ) == (4000, 7500, 9000)
    assert out["cost_per_booking_cents"] == 1_500 and out["cost_per_attended_cents"] == 2_000
    again = metric(api, g, leads=200, responded=150, bookings=80, attended=60, spend=90_000)
    assert (
        again["id"] == out["id"] and again["lead_to_booking_bps"] == 4000
    )  # substitui, não duplica
    rows = api.req("GET", f"/api/v1/acquisition/metrics?month={M}").json()
    assert len(rows) == 1 and rows[0]["leads"] == 200 and rows[0]["has_data"]
    for bad in (
        {"bookings": 101},
        {"responded": 101},
        {"attended": 41},
        {"leads": -1},
        {"spend": -5},
    ):
        metric(api, g, **bad, expect=422)


def test_unknown_or_foreign_channel_is_404(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    ch = channel(a)
    r = b.req(
        "PUT",
        "/api/v1/acquisition/metrics",
        json={"month": M, "channel_id": ch["id"], "leads_count": 1},
    )
    assert r.status_code == 404
    assert (
        b.req(
            "PATCH", f"/api/v1/acquisition/channels/{ch['id']}", json={"active": False}
        ).status_code
        == 404
    )


def test_funnel_totals_are_recomputed_not_averaged(make_user):
    api = make_user("a@x.com")
    g, ig = channel(api), channel(api, "Instagram", "INSTAGRAM")
    metric(api, g, leads=100, responded=100, bookings=50, attended=50, spend=100_000)
    metric(api, ig, leads=10, responded=10, bookings=1, attended=1, spend=5_000)
    f = funnel(api)
    t = f["total"]
    assert (t["leads"], t["bookings"], t["attended"], t["spend_cents"]) == (110, 51, 51, 105_000)
    assert t["lead_to_booking_bps"] == 51 * 10_000 // 110  # 4636, e não a média de 5000 e 1000
    assert t["cost_per_booking_cents"] == 2_059
    assert [c["channel"]["name"] for c in f["by_channel"]] == ["Google", "Instagram"]
    assert f["has_data"] and f["messages"] == []


@pytest.mark.parametrize(
    "counts,stage",
    [
        ((100, 90, 20, 18), "BOOKING"),
        ((100, 40, 60, 54), "RESPONSE_TIME"),
        ((100, 90, 60, 20), "ATTENDANCE"),
    ],
)
def test_bottleneck_and_allowed_recommendations(make_user, counts, stage):
    api = make_user("a@x.com")
    leads, responded, bookings, attended = counts
    metric(
        api,
        channel(api),
        leads=leads,
        responded=responded,
        bookings=bookings,
        attended=attended,
        spend=0,
    )
    b = funnel(api)["bottleneck"]
    assert (
        b["stage"] == stage
        and len(b["recommendations"]) >= 2
        and "referência de mercado" in b["note"]
    )
    text = " ".join(b["recommendations"]).lower()
    assert not any(w in text for w in ("lista", "compre", "garant", "spam", "comprar"))


def test_no_data_has_no_bottleneck_and_small_volume_is_flagged(make_user):
    api = make_user("a@x.com")
    f = funnel(api)
    assert f["has_data"] is False and f["bottleneck"] is None and f["total"]["leads"] == 0
    metric(api, channel(api), leads=10, responded=8, bookings=4, attended=3)
    assert any("Poucos contatos" in m for m in funnel(api)["messages"])


def test_conversion_drop_against_history_and_alert(make_user):
    api = make_user("a@x.com")
    g = channel(api)
    for mo in ("2026-07", "2026-08", "2026-09"):
        metric(api, g, month=mo, leads=100, responded=80, bookings=40, attended=30)
    metric(api, g, leads=100, responded=80, bookings=25, attended=20)
    f = funnel(api)
    assert f["conversion_drop_bps"] == 3750 and f["history_months"] == 3
    assert any("caiu 38%" in m or "caiu 37%" in m for m in f["messages"])
    a = api.req("GET", f"/api/v1/alerts?month={M}&as_of=2026-10-20").json()["alerts"]
    drop = next(x for x in a if x["kind"] == "CONVERSION_DROP")
    assert drop["route"] == "/app/acquisition" and "100 contatos" in drop["detail"]


def test_small_changes_and_low_volume_do_not_alert(make_user):
    api = make_user("a@x.com")
    g = channel(api)
    for mo in ("2026-08", "2026-09"):
        metric(api, g, month=mo, leads=100, responded=80, bookings=40, attended=30)
    metric(api, g, leads=100, responded=80, bookings=35, attended=30)  # queda de 12,5%
    assert funnel(api)["conversion_drop_bps"] is None
    low = make_user("b@x.com")
    lg = channel(low)
    metric(low, lg, month="2026-09", leads=100, responded=80, bookings=40, attended=30)
    metric(low, lg, leads=10, responded=8, bookings=1, attended=1)  # poucos contatos
    assert funnel(low)["conversion_drop_bps"] is None
    kinds = [
        x["kind"]
        for x in low.req("GET", f"/api/v1/alerts?month={M}&as_of=2026-10-20").json()["alerts"]
    ]
    assert "CONVERSION_DROP" not in kinds


def test_revenue_attribution_is_transparent_and_hidden_without_clinic_access(make_user):
    owner, sec = make_user("o@x.com"), make_user("s@x.com")
    _member(owner, sec, "ASSISTANT")
    service(owner)
    metric(owner, channel(owner), leads=100, responded=90, bookings=40, attended=30, spend=60_000)
    a = funnel(owner)["attribution"]
    assert a["revenue_per_attended_cents"] == 30_000 and a["attributed_revenue_cents"] == 900_000
    assert a["roi_bps"] == (900_000 - 60_000) * 10_000 // 60_000 and "informou" in a["note"]
    assert a["basis"] == "Consulta inicial"
    ch = funnel(owner)["by_channel"][0]
    assert ch["attributed_revenue_cents"] == 900_000 and ch["roi_bps"] == a["roi_bps"]
    s = funnel(sec, org_id=owner.org_id)  # secretária vê o funil, mas não receita nem preços
    assert s["attribution"] is None and "attributed_revenue_cents" not in s["by_channel"][0]
    assert s["total"]["leads"] == 100


def test_attribution_with_no_spend_has_no_roi(make_user):
    api = make_user("a@x.com")
    service(api)
    metric(
        api,
        channel(api, "Indicação", "REFERRAL"),
        leads=20,
        responded=20,
        bookings=10,
        attended=8,
        spend=0,
    )
    a = funnel(api)["attribution"]
    assert a["roi_bps"] is None and a["attributed_revenue_cents"] == 240_000


def test_secretary_leave_reduces_coverage(make_user):
    api = make_user("a@x.com")
    metric(api, channel(api))
    assert funnel(api)["secretary_coverage_bps"] == 10_000
    api.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-10-05",
        "end_date": "2026-10-18", "coverage_factor_bps": 0})  # fmt: skip
    f = funnel(api)
    assert f["secretary_coverage_bps"] == 10_000 - 14 * 10_000 // 31 == 5_484
    assert f["secretary_leaves"][0]["days"] == 14 and any("cobertura" in m for m in f["messages"])
    half = make_user("b@x.com")
    half.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-10-05",
        "end_date": "2026-10-18", "coverage_factor_bps": 5_000})  # fmt: skip
    assert funnel(half)["secretary_coverage_bps"] == 10_000 - 14 * 5_000 // 31
    cross = make_user("c@x.com")  # licença que atravessa o mês conta só os dias dentro dele
    cross.req("POST", "/api/v1/leaves", json={"staff_role": "SECRETARY", "start_date": "2026-09-25",
        "end_date": "2026-10-03", "coverage_factor_bps": 0})  # fmt: skip
    assert funnel(cross)["secretary_leaves"][0]["days"] == 3


def test_plan_estimates_leads_and_checks_the_agenda_first(make_user):
    api = make_user("a@x.com")
    svc = service(api)
    metric(api, channel(api), leads=100, responded=80, bookings=40, attended=30)
    r = api.req("GET", f"/api/v1/acquisition/plan?month={M}&target_new_patients=15").json()
    assert (r["bookings_needed"], r["leads_needed"], r["enough_data"]) == (20, 50, True)
    assert r["rates"] == {"lead_to_booking_bps": 4000, "booking_to_attendance_bps": 7500}
    assert any(
        "cheia" in m for m in r["messages"]
    )  # sem disponibilidade cadastrada: não recomenda captar
    assert r["capacity"]["new_patients_remaining"] == 0
    api.req(
        "POST",
        "/api/v1/availability",
        json={"starts_at": "2026-10-06T08:00:00-03:00", "ends_at": "2026-10-06T12:00:00-03:00"},
    )
    r2 = api.req(
        "GET", f"/api/v1/acquisition/plan?month={M}&target_new_patients=15&service_id={svc['id']}"
    ).json()
    assert r2["capacity"]["new_patients_remaining"] == 4
    assert any("comporta só 4" in m for m in r2["messages"])
    ok = api.req("GET", f"/api/v1/acquisition/plan?month={M}&target_new_patients=3").json()
    assert not any("comporta" in m or "cheia" in m for m in ok["messages"])


def test_plan_without_history_or_with_thin_data(make_user):
    api = make_user("a@x.com")
    r = api.req("GET", f"/api/v1/acquisition/plan?month={M}&target_new_patients=5").json()
    assert r["enough_data"] is False and "Registre" in r["message"]
    metric(api, channel(api), leads=10, responded=8, bookings=4, attended=3)
    thin = api.req("GET", f"/api/v1/acquisition/plan?month={M}&target_new_patients=5").json()
    assert thin["enough_data"] is False and any("frágil" in m for m in thin["messages"])
    zero = make_user("b@x.com")
    metric(zero, channel(zero), leads=10, responded=8, bookings=0, attended=0)
    z = zero.req("GET", f"/api/v1/acquisition/plan?month={M}&target_new_patients=5").json()
    assert z["leads_needed"] is None and any("Sem agendamentos" in m for m in z["messages"])


def test_plan_history_window_uses_previous_three_months(make_user):
    api = make_user("a@x.com")
    g = channel(api)
    metric(
        api, g, month="2026-06", leads=1000, responded=1000, bookings=1000, attended=1000
    )  # fora da janela
    metric(api, g, month="2026-08", leads=100, responded=80, bookings=50, attended=40)
    r = api.req("GET", f"/api/v1/acquisition/plan?month={M}&target_new_patients=8").json()
    assert r["months_used"] == 1 and r["rates"]["lead_to_booking_bps"] == 5000


def test_acquisition_report_and_monthly_result_section(make_user):
    api = make_user("a@x.com")
    service(api)
    g = channel(api)
    metric(api, g, leads=100, responded=90, bookings=40, attended=30, spend=60_000)
    r = api.req("POST", "/api/v1/reports/preview", json={"template": "ACQUISITION", "period": M})
    assert r.status_code == 200
    heads = [s["heading"] for s in r.json()["sections"]]
    assert any(h.startswith("Funil de") for h in heads) and any(
        h.startswith("Gargalo") for h in heads
    )
    assert "Receita atribuída" in heads
    gen = api.req(
        "POST",
        "/api/v1/reports/generate",
        json={"template": "ACQUISITION", "period": M, "format": "PDF"},
    )
    assert gen.status_code == 201
    empty = (
        make_user("b@x.com")
        .req("POST", "/api/v1/reports/preview", json={"template": "ACQUISITION", "period": M})
        .json()
    )
    assert empty["sections"][0]["heading"] == "Sem dados"
    mr = api.req(
        "POST",
        "/api/v1/reports/preview",
        json={"template": "MONTHLY_RESULT", "period": M, "as_of": "2026-10-20"},
    ).json()
    cap = next(s for s in mr["sections"] if s["heading"] == "Captação")
    assert dict(cap["kpis"])["Custo por paciente novo (comparecimento)"] == {"cents": 2_000}


# ---------- isolamento e papéis ----------


def test_cross_tenant_acquisition(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    metric(a, channel(a))
    assert b.req("GET", "/api/v1/acquisition/channels").json() == []
    assert funnel(b)["has_data"] is False
    assert b.req("GET", "/api/v1/acquisition/channels", org_id=a.org_id).status_code == 404
    assert b.req("GET", f"/api/v1/acquisition/funnel?month={M}", org_id=a.org_id).status_code == 404
    channel(b)  # mesmo nome em outra organização é permitido


def test_assistant_runs_the_funnel_accountant_cannot(make_user):
    owner, sec, acc = make_user("o@x.com"), make_user("s@x.com"), make_user("c@x.com")
    _member(owner, sec, "ASSISTANT")
    _member(owner, acc, "ACCOUNTANT")
    r = sec.req(
        "POST",
        "/api/v1/acquisition/channels",
        org_id=owner.org_id,
        json={"name": "Site", "channel_type": "SITE"},
    )
    assert r.status_code == 201
    m = sec.req("PUT", "/api/v1/acquisition/metrics", org_id=owner.org_id, json={"month": M, "channel_id": r.json()["id"],
        "leads_count": 10, "responded_in_time_count": 8, "bookings_count": 4, "attended_count": 3})  # fmt: skip
    assert m.status_code == 200
    assert funnel(owner)["total"]["leads"] == 10
    for method, url in [
        ("GET", "/api/v1/acquisition/channels"),
        ("GET", f"/api/v1/acquisition/funnel?month={M}"),
        ("GET", f"/api/v1/acquisition/plan?month={M}&target_new_patients=1"),
    ]:
        assert acc.req(method, url, org_id=owner.org_id).status_code == 403, url
    assert (
        acc.req(
            "POST",
            "/api/v1/acquisition/channels",
            org_id=owner.org_id,
            json={"name": "Z", "channel_type": "OTHER"},
        ).status_code
        == 403
    )
