"""Sprint 6: registrar o realizado e comparar esperado x obtido (US-007)."""

import pytest

from tests.conftest import SHIFT
from tests.test_clinic import POLICY, SERVICE

M = "2026-10"


def setup(api, work_units=4, service_units=10):
    w = api.req("POST", "/api/v1/work-sources", json=SHIFT).json()  # R$ 2.000, 15% de retenção
    p = api.req("POST", "/api/v1/return-policies", json=POLICY).json()
    s = api.req(
        "POST", "/api/v1/clinic-services", json={**SERVICE, "return_policy_id": p["id"]}
    ).json()
    if work_units is not None:
        plan(api, "WORK", w["id"], work_units, "GUARANTEED")
    if service_units is not None:
        plan(api, "SERVICE", s["id"], service_units, "PROBABLE")
    return w, s


def plan(api, kind, sid, units, certainty, month=M):
    r = api.req("PUT", "/api/v1/income-plans", json={"month": month, "source_type": kind,
        "source_id": sid, "units": units, "certainty": certainty})  # fmt: skip
    assert r.status_code == 200, r.text


def realize(api, kind, sid, units, amount, paid="2026-10-10", expect=201, **kw):
    r = api.req("POST", "/api/v1/realized", json={"source_type": kind, "source_id": sid, "month": M,
        "units": units, "amount_cents": amount, "paid_at": paid, **kw})  # fmt: skip
    assert r.status_code == expect, r.text
    return r.json()


def compare(api, month=M, **q):
    qs = "&".join(f"{k}={v}" for k, v in {"month": month, **q}.items())
    r = api.req("GET", f"/api/v1/realized/compare?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def src(res, name):
    return next(s for s in res["sources"] if s["name"] == name)


def test_realized_is_linked_to_the_source_and_compared_with_the_plan(make_user):
    api = make_user("a@x.com")
    w, _ = setup(api)
    t = realize(api, "WORK", w["id"], 2, 400_000, note="semana 1")
    assert t["status"] == "REALIZED" and t["units"] == 2 and t["work_source_id"] == w["id"]
    assert t["category"] == "plantoes" and t["description"] == "Plantão UPA · semana 1"
    row = src(compare(api), "Plantão UPA")
    assert (row["planned_units"], row["realized_units"]) == (4, 2)
    assert (row["planned_gross_cents"], row["realized_gross_cents"]) == (800_000, 400_000)
    assert row["variance_gross_cents"] == -400_000 and row["variance_pct_bps"] == 5_000
    assert row["status"] == "BEHIND"
    # líquido estimado do realizado = bruto − 15% de retenção (mesma do plano)
    assert row["realized_net_estimate_cents"] == 400_000 - 60_000
    assert row["planned_net_cents"] == 4 * 170_000


@pytest.mark.parametrize(
    "units,amount,expected",
    [
        (0, 0, "NOT_STARTED"),
        (2, 400_000, "BEHIND"),
        (4, 800_000, "ON_PLAN"),
        (5, 1_000_000, "AHEAD"),
    ],
)
def test_status_follows_realized_units(make_user, units, amount, expected):
    api = make_user("a@x.com")
    w, _ = setup(api)
    if units:
        realize(api, "WORK", w["id"], units, amount)
    assert src(compare(api), "Plantão UPA")["status"] == expected


def test_unplanned_realized_income_is_flagged(make_user):
    api = make_user("a@x.com")
    w, s = setup(api, service_units=None)
    realize(api, "SERVICE", s["id"], 3, 90_000)
    row = src(compare(api), "Consulta inicial")
    assert row["status"] == "UNPLANNED" and row["planned_units"] == 0 and row["realized_units"] == 3
    assert row["variance_pct_bps"] is None  # sem plano, sem percentual


def test_planned_never_becomes_realized_by_itself(make_user):
    api = make_user("a@x.com")
    w, _ = setup(api)
    before = api.req("GET", f"/api/v1/dashboard?month={M}").json()
    assert before["realized"]["income_cents"] == 0
    realize(api, "WORK", w["id"], 1, 200_000)
    after = api.req("GET", f"/api/v1/dashboard?month={M}").json()
    assert after["projection"] == before["projection"]  # o plano não muda
    assert after["realized"]["income_cents"] == 200_000  # só o que foi registrado


def test_duplicate_realization_is_blocked_unless_confirmed(make_user):
    api = make_user("a@x.com")
    w, _ = setup(api)
    realize(api, "WORK", w["id"], 1, 200_000)
    dup = realize(api, "WORK", w["id"], 1, 200_000, expect=409)
    assert "duplicado" in dup["detail"]
    realize(api, "WORK", w["id"], 1, 200_000, allow_duplicate=True)
    assert src(compare(api), "Plantão UPA")["realized_units"] == 2


def test_reversal_reduces_realized_units_and_amount(make_user):
    api = make_user("a@x.com")
    w, _ = setup(api)
    realize(api, "WORK", w["id"], 3, 600_000)
    r = api.req("POST", "/api/v1/transactions", json={"type": "INCOME", "status": "REALIZED",
        "competence_month": M, "paid_at": "2026-10-12", "amount_cents": 200_000, "category": "plantoes",
        "description": "Estorno plantão", "is_reversal": True, "work_source_id": w["id"], "units": 1})  # fmt: skip
    assert r.status_code == 201, r.text
    row = src(compare(api), "Plantão UPA")
    assert row["realized_units"] == 2 and row["realized_gross_cents"] == 400_000


def test_unlinked_income_is_listed_apart(make_user):
    api = make_user("a@x.com")
    setup(api)
    api.req("POST", "/api/v1/transactions", json={"type": "INCOME", "status": "REALIZED",
        "competence_month": M, "paid_at": "2026-10-03", "amount_cents": 150_000,
        "category": "consultas", "description": "Particular"})  # fmt: skip
    res = compare(api)
    assert (
        res["totals"]["unlinked_income_cents"] == 150_000
        and res["totals"]["realized_gross_cents"] == 0
    )
    assert any("sem vínculo" in n for n in res["notes"])


def test_expenses_planned_vs_realized_by_category(make_user):
    api = make_user("a@x.com")
    setup(api)

    def tx(**o):
        return api.req(
            "POST", "/api/v1/transactions", json={"type": "EXPENSE", "competence_month": M, **o}
        )

    tx(
        status="REALIZED",
        paid_at="2026-10-05",
        amount_cents=250_000,
        category="aluguel",
        description="Aluguel",
    )
    tx(status="PLANNED", amount_cents=90_000, category="software", description="Sistema")
    api.req("POST", "/api/v1/recurrences", json={"description": "Internet", "category": "utilidades",
        "amount_cents": 20_000, "frequency": "MONTHLY", "start_date": "2026-01-15"})  # fmt: skip
    rows = {e["category"]: e for e in compare(api)["expenses"]}
    assert (rows["aluguel"]["planned_cents"], rows["aluguel"]["realized_cents"]) == (0, 250_000)
    assert (rows["software"]["planned_cents"], rows["software"]["realized_cents"]) == (90_000, 0)
    assert rows["utilidades"]["planned_cents"] == 20_000  # recorrência projetada
    t = compare(api)["totals"]
    assert t["planned_expenses_cents"] == 110_000 and t["realized_expenses_cents"] == 250_000


def test_pace_depends_on_as_of(make_user):
    api = make_user("a@x.com")
    w, _ = setup(api)
    realize(api, "WORK", w["id"], 2, 400_000)
    mid = compare(api, as_of="2026-10-16")
    assert (
        mid["phase"] == "OPEN"
        and mid["pace"]["days_elapsed"] == 16
        and mid["pace"]["days_in_month"] == 31
    )
    assert mid["pace"]["elapsed_pct_bps"] == 16 * 10_000 // 31
    assert mid["pace"]["realized_vs_planned_gross_pct_bps"] == 400_000 * 10_000 // (
        800_000 + 10 * 30_000
    )
    assert compare(api, as_of="2026-11-02")["phase"] == "CLOSED"
    fut = compare(api, as_of="2026-09-30")
    assert fut["phase"] == "FUTURE" and fut["pace"]["days_elapsed"] == 0


def test_realize_a_planned_transaction(make_user):
    api = make_user("a@x.com")
    t = api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED",
        "competence_month": M, "amount_cents": 90_000, "category": "software", "description": "Sistema"}).json()  # fmt: skip
    assert (
        api.req("GET", f"/api/v1/finances/summary?month={M}").json()["competence"][
            "expense_realized_cents"
        ]
        == 0
    )
    r = api.req("POST", f"/api/v1/transactions/{t['id']}/realize", json={"paid_at": "2026-10-07"})
    assert (
        r.status_code == 200
        and r.json()["status"] == "REALIZED"
        and r.json()["paid_at"] == "2026-10-07"
    )
    s = api.req("GET", f"/api/v1/finances/summary?month={M}").json()
    assert (
        s["competence"]["expense_realized_cents"] == 90_000
        and s["competence"]["expense_planned_cents"] == 0
    )
    assert s["cash"]["expense_paid_cents"] == 90_000
    assert (
        api.req(
            "POST", f"/api/v1/transactions/{t['id']}/realize", json={"paid_at": "2026-10-07"}
        ).status_code
        == 409
    )


def test_realize_blocks_if_it_would_duplicate_a_realized_row(make_user):
    api = make_user("a@x.com")
    base = {
        "type": "EXPENSE",
        "competence_month": M,
        "amount_cents": 90_000,
        "category": "software",
        "description": "Sistema",
    }
    api.req(
        "POST", "/api/v1/transactions", json={**base, "status": "REALIZED", "paid_at": "2026-10-07"}
    )
    planned = api.req("POST", "/api/v1/transactions", json={**base, "status": "PLANNED"}).json()
    r = api.req(
        "POST", f"/api/v1/transactions/{planned['id']}/realize", json={"paid_at": "2026-10-07"}
    )
    assert r.status_code == 409


@pytest.mark.parametrize(
    "bad",
    [
        {"units": 0},
        {"amount_cents": 0},
        {"amount_cents": -5},
        {"month": "2026-13"},
        {"paid_at": "ontem"},
    ],
)
def test_invalid_realization_rejected(make_user, bad):
    api = make_user("a@x.com")
    w, _ = setup(api)
    body = {
        "source_type": "WORK",
        "source_id": w["id"],
        "month": M,
        "units": 1,
        "amount_cents": 1,
        "paid_at": "2026-10-10",
        **bad,
    }
    assert api.req("POST", "/api/v1/realized", json=body).status_code == 422


def test_transaction_cannot_link_both_work_and_service(make_user):
    api = make_user("a@x.com")
    w, s = setup(api)
    r = api.req("POST", "/api/v1/transactions", json={"type": "INCOME", "status": "REALIZED",
        "competence_month": M, "paid_at": "2026-10-10", "amount_cents": 1, "category": "consultas",
        "description": "x", "work_source_id": w["id"], "clinic_service_id": s["id"]})  # fmt: skip
    assert r.status_code == 422


# ---------- isolamento e papéis ----------


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    assert (
        owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role}).status_code
        == 201
    )


def test_cross_tenant_realized(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    w, s = setup(a)
    realize(b, "WORK", w["id"], 1, 100, expect=422)
    realize(b, "SERVICE", s["id"], 1, 100, expect=422)
    realize(a, "WORK", w["id"], 2, 400_000)
    res = compare(b)
    assert res["sources"] == [] and res["totals"]["realized_gross_cents"] == 0
    assert b.req("GET", f"/api/v1/realized/compare?month={M}", org_id=a.org_id).status_code == 404
    t = a.req("GET", f"/api/v1/transactions?month={M}").json()[0]
    assert (
        b.req(
            "POST", f"/api/v1/transactions/{t['id']}/realize", json={"paid_at": "2026-10-10"}
        ).status_code
        == 404
    )
    # B não consegue vincular uma receita sua ao trabalho de A
    r = b.req("POST", "/api/v1/transactions", json={"type": "INCOME", "status": "REALIZED",
        "competence_month": M, "paid_at": "2026-10-10", "amount_cents": 1, "category": "plantoes",
        "description": "x", "work_source_id": w["id"]})  # fmt: skip
    assert r.status_code == 422


def test_other_physician_work_is_not_mine(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _member(owner, doc, "PHYSICIAN")
    w = owner.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    r = doc.req("POST", "/api/v1/realized", org_id=owner.org_id, json={"source_type": "WORK",
        "source_id": w["id"], "month": M, "units": 1, "amount_cents": 100, "paid_at": "2026-10-10"})  # fmt: skip
    assert r.status_code == 422


@pytest.mark.parametrize("role", ["ASSISTANT", "ACCOUNTANT", "MANAGER"])
def test_roles_without_personal_income_cannot_register_or_compare(make_user, role):
    owner, other = make_user("o@x.com"), make_user("x@x.com")
    _member(owner, other, role)
    w, _ = setup(owner)
    body = {
        "source_type": "WORK",
        "source_id": w["id"],
        "month": M,
        "units": 1,
        "amount_cents": 100,
        "paid_at": "2026-10-10",
    }
    assert other.req("POST", "/api/v1/realized", org_id=owner.org_id, json=body).status_code == 403
    assert (
        other.req("GET", f"/api/v1/realized/compare?month={M}", org_id=owner.org_id).status_code
        == 403
    )
