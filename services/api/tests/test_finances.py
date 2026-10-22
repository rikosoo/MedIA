"""Despesas, competência x caixa, estornos, duplicidade e recorrências planejadas."""


def tx(**over):
    base = {
        "type": "EXPENSE",
        "status": "REALIZED",
        "competence_month": "2026-10",
        "paid_at": "2026-10-05",
        "amount_cents": 250_000,
        "category": "aluguel",
        "description": "Aluguel da sala",
    }
    return {**base, **over}


def post(api, **over):
    return api.req("POST", "/api/v1/transactions", json=tx(**over))


def summary(api, month="2026-10", **kw):
    return api.req("GET", f"/api/v1/finances/summary?month={month}", **kw).json()


def test_competence_vs_cash(make_user):
    """PRD 20.1.4: serviço de setembro recebido em outubro."""
    api = make_user("a@x.com")
    post(
        api, type="INCOME", category="consultas", competence_month="2026-09",
        paid_at="2026-10-03", amount_cents=1_000_000, description="Convênio setembro",
    )  # fmt: skip
    sep, octo = summary(api, "2026-09"), summary(api, "2026-10")
    assert sep["competence"]["income_realized_cents"] == 1_000_000
    assert sep["cash"]["income_received_cents"] == 0
    assert octo["competence"]["income_realized_cents"] == 0
    assert octo["cash"]["income_received_cents"] == 1_000_000


def test_refund_sign_is_shown_and_reduces_expense(make_user):
    """PRD 20.1.3: estorno exibido com o sinal correto."""
    api = make_user("a@x.com")
    post(api)
    r = post(api, is_reversal=True, amount_cents=50_000, description="Estorno aluguel").json()
    assert r["signed_cents"] == 50_000  # estorno de despesa melhora o resultado
    s = summary(api)["competence"]
    assert s["expense_realized_cents"] == 200_000
    assert s["result_realized_cents"] == -200_000  # déficit visível, sem esconder o sinal
    inc = post(api, type="INCOME", category="consultas", is_reversal=True, description="x").json()
    assert inc["signed_cents"] < 0


def test_planned_is_not_realized(make_user):
    api = make_user("a@x.com")
    post(api, status="PLANNED", paid_at=None, amount_cents=90_000, category="software",
         description="Assinatura anual")  # fmt: skip
    c = summary(api)["competence"]
    assert c["expense_realized_cents"] == 0 and c["expense_planned_cents"] == 90_000
    assert summary(api)["cash"]["expense_paid_cents"] == 0


def test_status_paid_at_consistency(make_user):
    api = make_user("a@x.com")
    assert post(api, paid_at=None).status_code == 422
    assert post(api, status="PLANNED").status_code == 422
    assert post(api, category="consultas").status_code == 422  # categoria de receita em despesa
    assert post(api, amount_cents=0).status_code == 422
    assert post(api, amount_cents=-5).status_code == 422
    assert post(api, competence_month="2026-13").status_code == 422


def test_duplicate_detected_unless_confirmed(make_user):
    api = make_user("a@x.com")
    assert post(api).status_code == 201
    assert post(api, description="  ALUGUEL   da sala ").status_code == 409
    assert post(api, allow_duplicate=True).status_code == 201
    assert len(api.req("GET", "/api/v1/transactions?month=2026-10").json()) == 2


def test_patch_and_delete(make_user):
    api = make_user("a@x.com")
    t = post(api).json()
    up = api.req("PATCH", f"/api/v1/transactions/{t['id']}", json={"amount_cents": 260_000})
    assert up.json()["amount_cents"] == 260_000
    bad = api.req("PATCH", f"/api/v1/transactions/{t['id']}", json={"paid_at": None})
    assert bad.status_code == 422  # REALIZED sem paid_at
    assert api.req("DELETE", f"/api/v1/transactions/{t['id']}").status_code == 204
    assert api.req("GET", "/api/v1/transactions").json() == []


def test_recurrence_projects_then_materializes_without_double_count(make_user):
    api = make_user("a@x.com")
    rec = api.req(
        "POST",
        "/api/v1/recurrences",
        json={
            "description": "Internet", "category": "utilidades", "amount_cents": 20_000,
            "frequency": "MONTHLY", "start_date": "2026-01-31",
        },
    ).json()  # fmt: skip
    c = summary(api, "2026-02")["competence"]
    assert c["expense_projected_from_recurrences_cents"] == 20_000
    assert c["expense_planned_cents"] == 20_000 and c["expense_realized_cents"] == 0
    assert summary(api, "2025-12")["competence"]["expense_planned_cents"] == 0

    m = api.req("POST", f"/api/v1/recurrences/{rec['id']}/materialize?month=2026-02").json()
    assert m["status"] == "PLANNED" and m["due_date"] == "2026-02-28"
    c = summary(api, "2026-02")["competence"]
    assert c["expense_planned_cents"] == 20_000  # não dobra
    assert c["expense_projected_from_recurrences_cents"] == 0
    again = api.req("POST", f"/api/v1/recurrences/{rec['id']}/materialize?month=2026-02")
    assert again.status_code == 409
    out = api.req("POST", f"/api/v1/recurrences/{rec['id']}/materialize?month=2025-12")
    assert out.status_code == 422

    api.req("DELETE", f"/api/v1/recurrences/{rec['id']}")
    assert summary(api, "2026-03")["competence"]["expense_planned_cents"] == 0


def test_expense_by_category(make_user):
    api = make_user("a@x.com")
    post(api)
    post(api, category="marketing", amount_cents=80_000, description="Anúncio")
    by = summary(api)["competence"]["expense_by_category_cents"]
    assert by == {"aluguel": 250_000, "marketing": 80_000}


def test_work_source_link_must_belong_to_org(make_user):
    from tests.conftest import SHIFT

    a, b = make_user("a@x.com"), make_user("b@x.com")
    w = a.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    assert post(a, work_source_id=w["id"]).status_code == 201
    assert post(b, work_source_id=w["id"]).status_code == 422


def test_cross_tenant_finances(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    t = post(a).json()
    rec = a.req(
        "POST",
        "/api/v1/recurrences",
        json={"description": "x", "category": "outros", "amount_cents": 1,
              "frequency": "YEARLY", "start_date": "2026-01-01"},
    ).json()  # fmt: skip
    assert (
        b.req("PATCH", f"/api/v1/transactions/{t['id']}", json={"description": "y"}).status_code
        == 404
    )
    assert b.req("DELETE", f"/api/v1/transactions/{t['id']}").status_code == 404
    assert b.req("DELETE", f"/api/v1/recurrences/{rec['id']}").status_code == 404
    assert (
        b.req("POST", f"/api/v1/recurrences/{rec['id']}/materialize?month=2026-01").status_code
        == 404
    )
    assert b.req("GET", "/api/v1/transactions").json() == []
    assert summary(b)["competence"]["expense_realized_cents"] == 0
    assert b.req("GET", "/api/v1/transactions", org_id=a.org_id).status_code == 404
    assert (
        b.req("GET", "/api/v1/finances/summary?month=2026-10", org_id=a.org_id).status_code == 404
    )
    # a continua intacta
    assert summary(a)["competence"]["expense_realized_cents"] == 250_000


def test_assistant_blocked_accountant_read_only(make_user):
    owner, sec, acc = make_user("o@x.com"), make_user("s@x.com"), make_user("c@x.com")
    for u, role in ((sec, "ASSISTANT"), (acc, "ACCOUNTANT")):
        owner.req(
            "POST", "/api/v1/team/members",
            json={"email": u.http.get("/api/v1/me").json()["email"], "role": role},
        )  # fmt: skip
    post(owner)
    for path in ("/api/v1/transactions", "/api/v1/finances/summary?month=2026-10",
                 "/api/v1/recurrences", "/api/v1/finance/categories"):  # fmt: skip
        assert sec.req("GET", path, org_id=owner.org_id).status_code == 403, path
        assert acc.req("GET", path, org_id=owner.org_id).status_code == 200, path
    assert (
        acc.req("POST", "/api/v1/transactions", org_id=owner.org_id, json=tx()).status_code == 403
    )
