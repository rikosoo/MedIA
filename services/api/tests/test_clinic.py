"""US-002: retornos (gratuito/parcial/pago), prazo, mês correto, entradas inválidas."""

import pytest

POLICY = {
    "name": "Retorno 30 dias",
    "occurrence_rate_bps": 7600,
    "delay_days": 30,
    "duration_mode": "PERCENT",
    "duration_percent_bps": 5000,
    "price_mode": "FREE",
}
SERVICE = {
    "name": "Consulta inicial",
    "price_cents": 30_000,
    "duration_minutes": 40,
    "cost_cents": 0,
    "fee_bps": 0,
    "no_show_rate_bps": 0,
}


def _setup(api, policy=None, service=None):
    p = api.req("POST", "/api/v1/return-policies", json={**POLICY, **(policy or {})})
    assert p.status_code == 201, p.text
    s = api.req(
        "POST",
        "/api/v1/clinic-services",
        json={**SERVICE, "return_policy_id": p.json()["id"], **(service or {})},
    )
    assert s.status_code == 201, s.text
    return p.json(), s.json()


def test_prd_example_55_2_minutes(make_user):
    api = make_user("a@x.com")
    _, s = _setup(api)
    cur = api.req("GET", f"/api/v1/clinic-services/{s['id']}/economics").json()["current"]
    assert cur["expected_minutes"] == "55.2"  # 40 + 76% x 20
    assert cur["return_minutes"] == 20  # 50% de 40
    assert cur["revenue_returns_cents"] == 0  # retorno gratuito
    assert cur["gross_revenue_cents"] == 30_000


def test_paid_partial_return_and_free_window(make_user):
    api = make_user("a@x.com")
    _, s = _setup(api, {"price_mode": "PARTIAL", "price_percent_bps": 5000})
    cur = api.req("GET", f"/api/v1/clinic-services/{s['id']}/economics").json()["current"]
    assert cur["return_price_cents"] == 15_000
    assert cur["revenue_returns_cents"] == 11_400
    # janela gratuita de 30 dias zera o preço (retorno em 30 dias)
    pid = s["return_policy_id"]
    api.req("PATCH", f"/api/v1/return-policies/{pid}", json={"free_within_days": 30})
    cur = api.req("GET", f"/api/v1/clinic-services/{s['id']}/economics").json()["current"]
    assert cur["return_price_cents"] == 0


def test_free_return_lands_next_month_without_revenue(make_user):
    api = make_user("a@x.com")
    _, s = _setup(api)
    r = api.req(
        "POST",
        "/api/v1/clinic/return-schedule",
        json={"service_id": s["id"], "consults": [{"date": "2026-10-20", "count": 10}]},
    ).json()
    assert r["months"] == [
        {"month": "2026-11", "expected_returns": "7.60", "minutes": "152.0", "revenue_cents": 0}
    ]


def test_price_simulation_delta(make_user):
    api = make_user("a@x.com")
    _, s = _setup(api)
    r = api.req("GET", f"/api/v1/clinic-services/{s['id']}/economics?price_cents=35000").json()
    assert r["simulated"]["gross_revenue_cents"] == 35_000
    assert r["delta_margin_cents"] == 5_000
    assert any("demanda constante" in a for a in r["assumptions"])


def test_capacity_floor(make_user):
    api = make_user("a@x.com")
    _, s = _setup(api)
    r = api.req(
        "POST", "/api/v1/clinic/capacity", json={"service_id": s["id"], "available_minutes": 6000}
    ).json()
    assert r["capacity_new_patients"] == 108 and r["is_approximation"] is True


def test_service_without_policy_has_no_returns(make_user):
    api = make_user("a@x.com")
    s = api.req("POST", "/api/v1/clinic-services", json=SERVICE).json()
    cur = api.req("GET", f"/api/v1/clinic-services/{s['id']}/economics").json()["current"]
    assert cur["expected_minutes"] == "40.0" and cur["expected_returns_bps"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"duration_minutes": 0},
        {"price_cents": -1},
        {"fee_bps": 10_001},
        {"no_show_rate_bps": -5},
        {"price_cents": 1.5},
    ],
)
def test_invalid_service_rejected(make_user, bad):
    api = make_user("a@x.com")
    assert api.req("POST", "/api/v1/clinic-services", json={**SERVICE, **bad}).status_code == 422


@pytest.mark.parametrize(
    "bad",
    [
        {"occurrence_rate_bps": 10_001},
        {"duration_mode": "MINUTES"},  # falta duration_minutes
        {"price_mode": "PARTIAL"},  # falta percentual
        {"price_mode": "CUSTOM"},
        {"delay_days": -1},
        {"price_mode": "NOPE"},
    ],
)
def test_invalid_policy_rejected(make_user, bad):
    api = make_user("a@x.com")
    r = api.req("POST", "/api/v1/return-policies", json={**POLICY, **bad})
    assert r.status_code == 422


def test_patch_policy_revalidates_whole_object(make_user):
    api = make_user("a@x.com")
    p, _ = _setup(api)
    r = api.req("PATCH", f"/api/v1/return-policies/{p['id']}", json={"price_mode": "CUSTOM"})
    assert r.status_code == 422
    ok = api.req(
        "PATCH",
        f"/api/v1/return-policies/{p['id']}",
        json={"price_mode": "CUSTOM", "price_cents": 9_900},
    )
    assert ok.status_code == 200 and ok.json()["price_cents"] == 9_900


def test_policy_in_use_cannot_be_deleted(make_user):
    api = make_user("a@x.com")
    p, s = _setup(api)
    assert api.req("DELETE", f"/api/v1/return-policies/{p['id']}").status_code == 409
    assert api.req("DELETE", f"/api/v1/clinic-services/{s['id']}").status_code == 204
    assert api.req("DELETE", f"/api/v1/return-policies/{p['id']}").status_code == 204


def test_cross_tenant_clinic(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    p, s = _setup(a)
    for method, url, body in [
        ("GET", f"/api/v1/clinic-services/{s['id']}", None),
        ("GET", f"/api/v1/clinic-services/{s['id']}/economics", None),
        ("PATCH", f"/api/v1/clinic-services/{s['id']}", {"name": "x"}),
        ("DELETE", f"/api/v1/clinic-services/{s['id']}", None),
        ("PATCH", f"/api/v1/return-policies/{p['id']}", {"name": "x"}),
        ("DELETE", f"/api/v1/return-policies/{p['id']}", None),
        ("POST", "/api/v1/clinic/capacity", {"service_id": s["id"], "available_minutes": 60}),
        ("POST", "/api/v1/clinic/return-schedule", {"service_id": s["id"], "consults": []}),
    ]:
        assert b.req(method, url, json=body).status_code == 404, (method, url)
    # B não pode apontar o próprio serviço para a política de A
    r = b.req("POST", "/api/v1/clinic-services", json={**SERVICE, "return_policy_id": p["id"]})
    assert r.status_code == 422
    assert b.req("GET", "/api/v1/clinic-services").json() == []
    assert b.req("GET", "/api/v1/clinic-services", org_id=a.org_id).status_code == 404


def test_roles_on_clinic(make_user):
    owner, sec, acc = make_user("o@x.com"), make_user("s@x.com"), make_user("c@x.com")
    for u, role in ((sec, "ASSISTANT"), (acc, "ACCOUNTANT")):
        owner.req(
            "POST",
            "/api/v1/team/members",
            json={"email": u.http.get("/api/v1/me").json()["email"], "role": role},
        )
    _, s = _setup(owner)
    assert sec.req("GET", "/api/v1/clinic-services", org_id=owner.org_id).status_code == 403
    assert acc.req("GET", "/api/v1/clinic-services", org_id=owner.org_id).status_code == 200
    r = acc.req("POST", "/api/v1/clinic-services", org_id=owner.org_id, json=SERVICE)
    assert r.status_code == 403
