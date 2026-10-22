import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app


@pytest.fixture
def app():
    # TEST_DATABASE_URL roda a suíte em PostgreSQL real (CI); sem ela, SQLite em memória.
    pg_url = os.environ.get("TEST_DATABASE_URL")
    if pg_url:
        engine = create_engine(pg_url)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    def override():
        with factory() as s:
            yield s

    application = create_app()
    application.dependency_overrides[get_db] = override
    application.state.session_factory = factory
    yield application
    engine.dispose()  # sem isto a suíte em PostgreSQL esgota as conexões


class Api:
    """Cliente autenticado com cookie + CSRF + organização ativa."""

    def __init__(self, app, email: str):
        self.http = TestClient(app)
        r = self.http.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "senha-bem-longa-123",
                "name": email.split("@")[0],
                "organization_name": f"Org de {email}",
                "accept_terms": True,
            },
        )
        assert r.status_code == 201, r.text
        self.csrf = r.json()["csrf_token"]
        self.user_id = r.json()["user_id"]
        self.org_id = self.http.get("/api/v1/me").json()["organizations"][0]["id"]

    def h(self, org_id: str | None = None) -> dict:
        return {"X-CSRF-Token": self.csrf, "X-Organization-Id": org_id or self.org_id}

    def req(self, method: str, url: str, org_id: str | None = None, **kw):
        return self.http.request(method, url, headers=self.h(org_id), **kw)


@pytest.fixture
def make_user(app):
    def _make(email: str) -> Api:
        return Api(app, email)

    return _make


SHIFT = {
    "name": "Plantão UPA",
    "work_type": "SHIFT",
    "regime": "PJ",
    "income": {
        "gross_cents": 200_000,
        "unit_type": "PER_EVENT",
        "duration_minutes": 720,
        "travel_minutes": 60,
        "tax_rate_bps": 1500,
    },
}
