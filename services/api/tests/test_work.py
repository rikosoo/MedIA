import pytest

from tests.conftest import SHIFT


def test_crud_and_hourly_values(make_user):
    api = make_user("a@x.com")
    r = api.req("POST", "/api/v1/work-sources", json=SHIFT)
    w = r.json()
    inc = w["income"]
    # 200.000 - 15% = 170.000 líquidos estimados; comprometido = 720 + 60 = 780 min
    assert inc["estimated_net_cents"] == 170_000
    assert inc["commitment_minutes"] == 780
    assert inc["net_per_active_hour_cents"] == 14_166
    assert inc["net_per_committed_hour_cents"] == 13_076
    assert w["tax_is_estimate"] is True

    upd = api.req("PATCH", f"/api/v1/work-sources/{w['id']}", json={"name": "Novo nome"}).json()
    assert upd["name"] == "Novo nome" and upd["income"]["gross_cents"] == 200_000

    api.req("PATCH", f"/api/v1/work-sources/{w['id']}", json={"status": "archived"})
    assert api.req("GET", "/api/v1/work-sources").json() == []
    assert len(api.req("GET", "/api/v1/work-sources?include_archived=true").json()) == 1

    assert api.req("DELETE", f"/api/v1/work-sources/{w['id']}").status_code == 204
    assert api.req("GET", f"/api/v1/work-sources/{w['id']}").status_code == 404


def test_no_time_means_no_hourly_value(make_user):
    api = make_user("a@x.com")
    body = {**SHIFT, "income": {"gross_cents": 100_000, "unit_type": "MONTHLY"}}
    inc = api.req("POST", "/api/v1/work-sources", json=body).json()["income"]
    assert inc["net_per_active_hour_cents"] is None


@pytest.mark.parametrize(
    "income",
    [
        {"gross_cents": -1, "unit_type": "PER_EVENT"},
        {"gross_cents": 1.5, "unit_type": "PER_EVENT"},
        {"gross_cents": 100, "unit_type": "PER_EVENT", "tax_rate_bps": 10_001},
        {
            "gross_cents": 100,
            "unit_type": "PER_EVENT",
            "duration_minutes": 600,
            "commitment_minutes": 60,
        },
        {"gross_cents": 100, "unit_type": "NOPE"},
    ],
)
def test_validation(make_user, income):
    api = make_user("a@x.com")
    assert (
        api.req("POST", "/api/v1/work-sources", json={**SHIFT, "income": income}).status_code == 422
    )


def test_audit_log_has_no_financial_values(app, make_user):
    from sqlalchemy import select

    from app.db.models import AuditLog

    api = make_user("a@x.com")
    api.req("POST", "/api/v1/work-sources", json=SHIFT)
    with app.state.session_factory() as s:
        logs = s.scalars(select(AuditLog)).all()
    assert {"auth.register", "work.create"} <= {x.action for x in logs}
    assert all("gross" not in str(x.metadata_json) for x in logs)
