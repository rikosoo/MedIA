"""US-010 e US-012: isolamento entre organizações e permissões por papel."""

from tests.conftest import SHIFT


def _create(api, **over):
    r = api.req("POST", "/api/v1/work-sources", json={**SHIFT, **over})
    assert r.status_code == 201, r.text
    return r.json()


def test_cross_tenant_read_update_delete_blocked(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    work = _create(a)
    wid = work["id"]

    # B tenta usar a organização de A: nem revela que existe.
    assert b.req("GET", "/api/v1/work-sources", org_id=a.org_id).status_code == 404
    assert b.req("POST", "/api/v1/work-sources", org_id=a.org_id, json=SHIFT).status_code == 404
    # B usa a própria org com o id do trabalho de A.
    assert b.req("GET", f"/api/v1/work-sources/{wid}").status_code == 404
    assert b.req("PATCH", f"/api/v1/work-sources/{wid}", json={"name": "x"}).status_code == 404
    assert b.req("DELETE", f"/api/v1/work-sources/{wid}").status_code == 404
    assert b.req("GET", "/api/v1/work-sources").json() == []
    assert b.req("GET", "/api/v1/team/members", org_id=a.org_id).status_code == 404
    assert b.req("GET", "/api/v1/organizations/current", org_id=a.org_id).status_code == 404
    # intacto para A
    assert a.req("GET", f"/api/v1/work-sources/{wid}").json()["name"] == "Plantão UPA"


def test_invalid_org_header(make_user):
    a = make_user("a@x.com")
    assert a.req("GET", "/api/v1/work-sources", org_id="nao-e-uuid").status_code == 404
    assert a.http.get("/api/v1/work-sources").status_code == 400


def _add(owner, other, role):
    r = owner.req("POST", "/api/v1/team/members", json={"email": other_email(other), "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def other_email(api):
    return api.http.get("/api/v1/me").json()["email"]


def test_assistant_cannot_see_income(make_user):
    owner, sec = make_user("owner@x.com"), make_user("sec@x.com")
    _create(owner)
    _add(owner, sec, "ASSISTANT")
    assert sec.req("GET", "/api/v1/work-sources", org_id=owner.org_id).status_code == 403
    assert (
        sec.req("POST", "/api/v1/work-sources", org_id=owner.org_id, json=SHIFT).status_code == 403
    )
    assert sec.req("GET", "/api/v1/team/members", org_id=owner.org_id).status_code == 403


def test_physician_sees_only_own_manager_sees_all(make_user):
    owner, doc, mgr = make_user("o@x.com"), make_user("d@x.com"), make_user("m@x.com")
    _add(owner, doc, "PHYSICIAN")
    _add(owner, mgr, "MANAGER")
    mine = _create(owner)
    theirs = doc.req("POST", "/api/v1/work-sources", org_id=owner.org_id, json=SHIFT).json()

    docs_view = doc.req("GET", "/api/v1/work-sources", org_id=owner.org_id).json()
    assert [w["id"] for w in docs_view] == [theirs["id"]]
    assert (
        doc.req("GET", f"/api/v1/work-sources/{mine['id']}", org_id=owner.org_id).status_code == 404
    )
    assert len(mgr.req("GET", "/api/v1/work-sources", org_id=owner.org_id).json()) == 2


def test_accountant_read_only(make_user):
    owner, acc = make_user("o@x.com"), make_user("c@x.com")
    _create(owner)
    _add(owner, acc, "ACCOUNTANT")
    assert len(acc.req("GET", "/api/v1/work-sources", org_id=owner.org_id).json()) == 1
    assert (
        acc.req("POST", "/api/v1/work-sources", org_id=owner.org_id, json=SHIFT).status_code == 403
    )


def test_only_owner_manages_team_and_revocation_is_immediate(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    m = _add(owner, doc, "PHYSICIAN")
    other = make_user("z@x.com")
    assert (
        doc.req(
            "POST",
            "/api/v1/team/members",
            org_id=owner.org_id,
            json={"email": other_email(other), "role": "OWNER"},
        ).status_code
        == 403
    )
    assert doc.req("GET", "/api/v1/work-sources", org_id=owner.org_id).status_code == 200
    assert owner.req("DELETE", f"/api/v1/team/members/{m['id']}").status_code == 204
    assert doc.req("GET", "/api/v1/work-sources", org_id=owner.org_id).status_code == 404


def test_cannot_remove_or_demote_last_owner(make_user):
    owner = make_user("o@x.com")
    me = owner.req("GET", "/api/v1/team/members").json()[0]
    assert (
        owner.req(
            "PATCH", f"/api/v1/team/members/{me['id']}", json={"role": "PHYSICIAN"}
        ).status_code
        == 409
    )
    assert owner.req("DELETE", f"/api/v1/team/members/{me['id']}").status_code == 409


def test_member_of_other_org_not_touchable(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    b_member = b.req("GET", "/api/v1/team/members").json()[0]
    assert a.req("DELETE", f"/api/v1/team/members/{b_member['id']}").status_code == 404


def test_only_owner_updates_org(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _add(owner, doc, "PHYSICIAN")
    assert (
        doc.req(
            "PATCH", "/api/v1/organizations/current", org_id=owner.org_id, json={"name": "X"}
        ).status_code
        == 403
    )
    assert (
        owner.req("PATCH", "/api/v1/organizations/current", json={"name": "Nova"}).json()["name"]
        == "Nova"
    )
