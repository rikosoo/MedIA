from fastapi.testclient import TestClient


def test_health(app):
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_register_login_logout(app, make_user):
    api = make_user("a@x.com")
    assert api.http.get("/api/v1/me").json()["email"] == "a@x.com"
    assert api.req("POST", "/api/v1/auth/logout").status_code == 204
    assert api.http.get("/api/v1/me").status_code == 401

    c = TestClient(app)
    bad = c.post("/api/v1/auth/login", json={"email": "a@x.com", "password": "errada-errada"})
    assert bad.status_code == 401
    ghost = c.post("/api/v1/auth/login", json={"email": "no@x.com", "password": "errada-errada"})
    assert ghost.status_code == 401 and ghost.json() == bad.json()
    ok = c.post("/api/v1/auth/login", json={"email": "A@x.com", "password": "senha-bem-longa-123"})
    assert ok.status_code == 200
    assert "httponly" in ok.headers["set-cookie"].lower()


def test_duplicate_email_and_weak_password(app, make_user):
    make_user("a@x.com")
    c = TestClient(app)
    base = {"email": "a@x.com", "name": "A", "organization_name": "O"}
    assert (
        c.post(
            "/api/v1/auth/register", json={**base, "password": "senha-bem-longa-123"}
        ).status_code
        == 409
    )
    assert (
        c.post(
            "/api/v1/auth/register", json={**base, "email": "b@x.com", "password": "curta"}
        ).status_code
        == 422
    )


def test_csrf_required_on_mutations(app, make_user):
    from tests.conftest import SHIFT

    api = make_user("a@x.com")
    r = api.http.post("/api/v1/work-sources", headers={"X-Organization-Id": api.org_id}, json=SHIFT)
    assert r.status_code == 403
    r = api.http.post(
        "/api/v1/work-sources",
        headers={"X-Organization-Id": api.org_id, "X-CSRF-Token": "forjado"},
        json=SHIFT,
    )
    assert r.status_code == 403


def test_unauthenticated_blocked(app):
    assert TestClient(app).get("/api/v1/work-sources").status_code == 401
