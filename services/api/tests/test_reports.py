"""Sprint 7: relatórios (PDF/CSV), retenção, link temporário e fechamento mensal."""

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from tests.test_goals import M, full_scenario, goal

AS_OF = "2026-10-20"


def gen(api, template="MONTHLY_RESULT", period=M, fmt="PDF", expect=201, **kw):
    r = api.req("POST", "/api/v1/reports/generate", json={"template": template, "period": period,
        "format": fmt, "as_of": AS_OF, **kw})  # fmt: skip
    assert r.status_code == expect, r.text
    return r.json()


def preview(api, template="MONTHLY_RESULT", period=M, expect=200):
    r = api.req(
        "POST",
        "/api/v1/reports/preview",
        json={"template": template, "period": period, "as_of": AS_OF},
    )
    assert r.status_code == expect, r.text
    return r.json()


def pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def sec(doc, heading):
    return next(s for s in doc["sections"] if s["heading"].startswith(heading))


def kpi(section, label):
    return dict((k, v) for k, v in section["kpis"])[label]


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    assert (
        owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role}).status_code
        == 201
    )


def test_monthly_result_numbers_match_the_dashboard(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api)
    doc = preview(api)
    resumo = sec(doc, "Resumo de Outubro de 2026")
    assert kpi(resumo, "Meta do mês") == {"cents": 3_000_000}
    assert kpi(resumo, "Projetado (planos)") == {"cents": 2_350_000}
    assert kpi(resumo, "  do qual garantido") == {"cents": 1_000_000}
    assert kpi(resumo, "Realizado (receitas - despesas lançadas)") == {"cents": 750_000}
    assert kpi(resumo, "Falta para a meta") == {"cents": 650_000}
    assert kpi(resumo, "Situação") == "Abaixo da meta"
    src = sec(doc, "Esperado x obtido")["tables"][0]["rows"]
    assert {r[0] for r in src} == {"Plantão UPA", "Consulta inicial"}
    exp = sec(doc, "Despesas")["tables"][0]["rows"]
    assert exp[-1][0] == "Total" and exp[-1][2] == {"cents": 250_000}
    assert any("nunca é somado" in n for n in sec(doc, "Como calculamos")["notes"])
    assert doc["disclaimer"] and "não é consultoria" in doc["disclaimer"].lower()


def test_next_month_commitments_and_suggestion(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api)
    api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED",
        "competence_month": "2026-11", "amount_cents": 120_000, "category": "software", "description": "Licença anual"})  # fmt: skip
    doc = preview(api)
    fut = sec(doc, "Compromissos futuros")
    assert kpi(fut, "Despesas já previstas em Novembro de 2026") == {
        "cents": 140_000
    }  # + recorrência
    assert any("Licença anual" in n for n in fut["notes"])
    nxt = sec(doc, "Próximo mês")["notes"]
    assert any("repetir a meta de R$ 30.000,00" in n for n in nxt)
    api.req("POST", "/api/v1/goals", json={"month": "2026-11", "target_cents": 3_500_000})
    assert any("já definida" in n for n in sec(preview(api), "Próximo mês")["notes"])


def test_pdf_is_a_real_pdf_with_the_report_numbers(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api)
    meta = gen(api)
    assert meta["format"] == "PDF" and meta["filename"] == "monthly_result-2026-10.pdf"
    r = api.req("GET", f"/api/v1/reports/{meta['id']}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) == meta["size_bytes"]
    assert (
        "no-store" in r.headers["cache-control"]
        and r.headers["x-content-type-options"] == "nosniff"
    )
    text = pdf_text(r.content)
    assert "Resultado mensal" in text and "R$ 23.500,00" in text and "R$ 30.000,00" in text
    assert "Plantão UPA" in text and "estimativas" in text.lower()


def test_pdf_survives_characters_outside_latin1(make_user):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "REALIZED", "competence_month": M,
        "paid_at": "2026-10-05", "amount_cents": 1_000, "category": "outros", "description": "Café ≈ 2 × 5 — 日本 <b>&</b>"})  # fmt: skip
    meta = gen(api, "EXPENSES")
    text = pdf_text(api.req("GET", f"/api/v1/reports/{meta['id']}/download").content)
    assert "Café" in text and "<b>" in text  # texto do usuário não vira marcação do PDF


def test_csv_report_is_spreadsheet_safe(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    for desc in ("=CMD|' /C calc'!A0", "@SOMA(A1)", "+1+1"):
        api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "REALIZED", "competence_month": M,
            "paid_at": "2026-10-06", "amount_cents": 500, "category": "outros", "description": desc})  # fmt: skip
    meta = gen(api, "EXPENSES", fmt="CSV")
    body = api.req("GET", f"/api/v1/reports/{meta['id']}/download").content.decode("utf-8")
    assert body.startswith("﻿") and meta["filename"].endswith(".csv")
    for line in body.splitlines():
        for cell in line.split(";"):
            assert not cell.startswith(("=", "@", "+")), cell
    assert "'=CMD" in body and "'@SOMA" in body and "'+1+1" in body
    assert "2500,00" in body  # números como números, com vírgula decimal


def test_every_template_previews_and_generates(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api)
    for t in api.req("GET", "/api/v1/reports/templates").json():
        assert t["allowed"]
        period = "2026" if t["period"] == "year" else M
        doc = preview(api, t["template"], period)
        assert doc["sections"] and doc["title"]
        for fmt in ("PDF", "CSV"):
            assert gen(api, t["template"], period, fmt)["size_bytes"] > 100


def test_year_report_has_twelve_months(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    goal(api)
    doc = preview(api, "YEAR", "2026")
    rows = sec(doc, "Mês a mês")["tables"][0]["rows"]
    assert [r[0] for r in rows][:2] == ["Janeiro", "Fevereiro"] and len(rows) == 12
    out = rows[9]
    assert out[0] == "Outubro" and out[2] == {"cents": 3_000_000} and out[3] == {"cents": 2_350_000}
    assert preview(api, "YEAR", "2026-10", expect=422) and preview(
        api, "EXPENSES", "2026", expect=422
    )


def test_accountant_report_lists_entries_and_warns_about_estimates(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    doc = preview(api, "ACCOUNTANT")
    resumo = sec(doc, "Resumo de")
    assert kpi(resumo, "Receitas realizadas (competência)") == {"cents": 1_000_000}
    assert kpi(resumo, "Despesas realizadas (competência)") == {"cents": 250_000}
    assert kpi(resumo, "Recebido no mês (caixa)") == {"cents": 1_000_000}
    lines = sec(doc, "Lançamentos realizados")["tables"][0]["rows"]
    assert len(lines) == 2 and {r[2] for r in lines} == {"Receita", "Despesa"}
    assert any(
        "não apurados" in n.lower() or "Não substituem" in n for n in sec(doc, "Retenções")["notes"]
    )


def test_optimization_report_with_and_without_runs(make_user):
    from tests.test_optimizer import acts, setup

    api = make_user("a@x.com")
    assert sec(preview(api, "OPTIMIZATION"), "Sem execuções")
    shift, oncall, svc = setup(api)
    r = api.req("POST", "/api/v1/optimizer/runs", json={"month": M, "target_cents": 4_600_000, "baseline_cents": 3_800_000,
        "activities": acts(shift, oncall, svc)})  # fmt: skip
    assert r.status_code == 201
    doc = preview(api, "OPTIMIZATION")
    assert sec(doc, "Cenários")["tables"][0]["rows"]
    api.req("POST", "/api/v1/optimizer/runs", json={"month": M, "target_cents": 9_900_000, "baseline_cents": 0,
        "activities": acts(shift, oncall, svc, shift_max=1)})  # fmt: skip
    bad = preview(api, "OPTIMIZATION")  # a execução mais recente é a inviável
    assert sec(bad, "Por que não fecha")["kpis"]


def test_retention_expiry_and_cleanup(make_user, app):
    from sqlalchemy import select

    from app.db.models import ReportJob

    api = make_user("a@x.com")
    meta = gen(api, "EXPENSES")
    assert datetime.fromisoformat(meta["expires_at"]) > datetime.now(UTC) + timedelta(days=29)
    with app.state.session_factory() as s:
        row = s.scalar(select(ReportJob))
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        s.commit()
    assert api.req("GET", f"/api/v1/reports/{meta['id']}/download").status_code == 404
    assert api.req("GET", "/api/v1/reports").json() == []
    gen(api, "EXPENSES")  # a próxima geração remove o conteúdo vencido
    with app.state.session_factory() as s:
        old = s.get(ReportJob, uuid.UUID(meta["id"]))
        assert old.content == b"" and old.deleted_at is not None


def test_list_and_delete_reports(make_user):
    api = make_user("a@x.com")
    a, b = gen(api, "EXPENSES", fmt="CSV"), gen(api, "EXPENSES", fmt="PDF")
    assert [r["id"] for r in api.req("GET", "/api/v1/reports").json()] == [b["id"], a["id"]]
    assert api.req("DELETE", f"/api/v1/reports/{a['id']}").status_code == 204
    assert [r["id"] for r in api.req("GET", "/api/v1/reports").json()] == [b["id"]]
    assert api.req("GET", f"/api/v1/reports/{a['id']}/download").status_code == 404


@pytest.mark.parametrize(
    "bad", [{"template": "NOPE"}, {"period": "2026-13"}, {"period": "out/26"}, {"format": "DOCX"}]
)
def test_invalid_report_requests(make_user, bad):
    api = make_user("a@x.com")
    body = {"template": "EXPENSES", "period": M, "format": "PDF", **bad}
    assert api.req("POST", "/api/v1/reports/generate", json=body).status_code in (422,)


# ---------- link temporário ----------


def anon(app):
    return TestClient(app)  # sem cookie: simula o contador abrindo o link


def test_share_link_works_without_login_then_can_be_revoked(make_user, app):
    api = make_user("a@x.com")
    full_scenario(api)
    meta = gen(api, "ACCOUNTANT")
    sh = api.req("POST", f"/api/v1/reports/{meta['id']}/share", json={"days": 3}).json()
    assert sh["token"] and sh["path"].endswith(sh["token"]) and not sh["revoked"]
    exp = datetime.fromisoformat(sh["expires_at"])
    assert timedelta(days=2, hours=23) < exp - datetime.now(UTC) <= timedelta(days=3)
    pub = anon(app).get(sh["path"])
    assert (
        pub.status_code == 200
        and pub.content.startswith(b"%PDF")
        and "no-store" in pub.headers["cache-control"]
    )
    anon(app).get(sh["path"])
    listing = api.req("GET", f"/api/v1/reports/{meta['id']}/shares").json()
    assert listing[0]["access_count"] == 2 and listing[0]["last_access_at"]
    assert "token" not in listing[0] and sh["token"] not in str(
        listing
    )  # o token só aparece na criação
    assert api.req("DELETE", f"/api/v1/report-shares/{sh['id']}").status_code == 204
    assert anon(app).get(sh["path"]).status_code == 404


def test_share_link_failures_are_indistinguishable(make_user, app):
    from sqlalchemy import select

    from app.db.models import ReportShare

    api = make_user("a@x.com")
    meta = gen(api, "EXPENSES")
    sh = api.req("POST", f"/api/v1/reports/{meta['id']}/share", json={}).json()
    wrong = anon(app).get("/api/v1/shared-reports/" + "x" * 43)
    assert wrong.status_code == 404
    with app.state.session_factory() as s:
        row = s.scalar(select(ReportShare))
        assert (
            row.token_hash != sh["token"] and len(row.token_hash) == 64
        )  # só o hash fica guardado
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()
    expired = anon(app).get(sh["path"])
    assert expired.status_code == 404 and expired.json() == wrong.json()


def test_share_days_are_limited_and_deleting_report_kills_links(make_user, app):
    api = make_user("a@x.com")
    meta = gen(api, "EXPENSES")
    for days in (0, 31, -1):
        assert (
            api.req("POST", f"/api/v1/reports/{meta['id']}/share", json={"days": days}).status_code
            == 422
        )
    sh = api.req("POST", f"/api/v1/reports/{meta['id']}/share", json={"days": 30}).json()
    assert anon(app).get(sh["path"]).status_code == 200
    api.req("DELETE", f"/api/v1/reports/{meta['id']}")
    assert anon(app).get(sh["path"]).status_code == 404


def test_share_access_is_audited_without_the_file(make_user, app):
    from sqlalchemy import select

    from app.db.models import AuditLog

    api = make_user("a@x.com")
    meta = gen(api, "EXPENSES")
    sh = api.req("POST", f"/api/v1/reports/{meta['id']}/share", json={}).json()
    anon(app).get(sh["path"])
    with app.state.session_factory() as s:
        logs = {a.action: a for a in s.scalars(select(AuditLog))}
    assert {"report.generate", "report.share", "report.shared_access"} <= set(logs)
    assert "ip_hash" in logs["report.shared_access"].metadata_json
    assert sh["token"] not in str([a.metadata_json for a in logs.values()])


# ---------- permissões e isolamento ----------


def test_templates_follow_the_role(make_user):
    owner, acc, sec_ = make_user("o@x.com"), make_user("c@x.com"), make_user("s@x.com")
    _member(owner, acc, "ACCOUNTANT")
    _member(owner, sec_, "ASSISTANT")

    def allowed(u):
        r = u.req("GET", "/api/v1/reports/templates", org_id=owner.org_id).json()
        return {t["template"] for t in r if t["allowed"]}

    assert len(allowed(owner)) == 7
    assert allowed(acc) == {"EXPENSES"}
    assert allowed(sec_) == {"ACQUISITION"}
    body = {"template": "MONTHLY_RESULT", "period": M, "format": "PDF"}
    assert (
        acc.req("POST", "/api/v1/reports/generate", org_id=owner.org_id, json=body).status_code
        == 403
    )
    assert (
        sec_.req(
            "POST",
            "/api/v1/reports/generate",
            org_id=owner.org_id,
            json={**body, "template": "EXPENSES"},
        ).status_code
        == 403
    )
    assert (
        acc.req(
            "POST",
            "/api/v1/reports/generate",
            org_id=owner.org_id,
            json={**body, "template": "ACCOUNTANT"},
        ).status_code
        == 403
    )
    ok = acc.req(
        "POST",
        "/api/v1/reports/generate",
        org_id=owner.org_id,
        json={**body, "template": "EXPENSES"},
    )
    assert ok.status_code == 201


def test_reports_are_personal_and_tenant_isolated(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    meta = gen(a, "EXPENSES")
    sh = a.req("POST", f"/api/v1/reports/{meta['id']}/share", json={}).json()
    assert b.req("GET", f"/api/v1/reports/{meta['id']}/download").status_code == 404
    assert b.req("DELETE", f"/api/v1/reports/{meta['id']}").status_code == 404
    assert b.req("POST", f"/api/v1/reports/{meta['id']}/share", json={}).status_code == 404
    assert b.req("GET", f"/api/v1/reports/{meta['id']}/shares").status_code == 404
    assert b.req("DELETE", f"/api/v1/report-shares/{sh['id']}").status_code == 404
    assert b.req("GET", "/api/v1/reports").json() == []
    assert b.req("GET", "/api/v1/reports", org_id=a.org_id).status_code == 404
    # B não vê os números de A no próprio relatório
    full_scenario(a)
    doc = preview(b, "EXPENSES")
    assert kpi(sec(doc, "Despesas de"), "Realizadas") == {"cents": 0}


def test_other_members_cannot_download_my_report(make_user):
    owner, doc = make_user("o@x.com"), make_user("d@x.com")
    _member(owner, doc, "PHYSICIAN")
    meta = gen(owner, "EXPENSES")
    assert (
        doc.req("GET", f"/api/v1/reports/{meta['id']}/download", org_id=owner.org_id).status_code
        == 404
    )
    assert doc.req("GET", "/api/v1/reports", org_id=owner.org_id).json() == []


# ---------- fechamento mensal ----------


def test_closing_checklist_progress(make_user):
    api = make_user("a@x.com")
    full_scenario(api)
    c = api.req("GET", f"/api/v1/closing?month={M}&as_of=2026-11-02").json()
    by = {i["code"]: i for i in c["items"]}
    assert c["phase"] == "CLOSED" and not c["ready_to_close"]
    assert not by["PENDING_PLANNED"]["done"] and not by["UNLINKED_INCOME"]["done"]
    assert not by["REPORT_GENERATED"]["done"] and not by["NEXT_GOAL"]["done"]
    assert by["SCHEDULE_OK"]["done"] and by["PENDING_PLANNED"]["route"] == "/app/finances"
    base_done = c["done"]
    planned = next(
        t
        for t in api.req("GET", f"/api/v1/transactions?month={M}").json()
        if t["status"] == "PLANNED"
    )
    api.req("POST", f"/api/v1/transactions/{planned['id']}/realize", json={"paid_at": "2026-10-20"})
    gen(api)
    goal(api, month="2026-11", target_cents=1)
    c2 = api.req("GET", f"/api/v1/closing?month={M}&as_of=2026-11-02").json()
    by2 = {i["code"]: i for i in c2["items"]}
    assert (
        by2["PENDING_PLANNED"]["done"]
        and by2["REPORT_GENERATED"]["done"]
        and by2["NEXT_GOAL"]["done"]
    )
    assert c2["done"] == base_done + 3 and c2["total"] == c["total"]


def test_closing_only_shows_items_the_role_can_see(make_user):
    owner, sec_ = make_user("o@x.com"), make_user("s@x.com")
    _member(owner, sec_, "ASSISTANT")
    full_scenario(owner)
    c = sec_.req("GET", f"/api/v1/closing?month={M}", org_id=owner.org_id).json()
    assert [i["code"] for i in c["items"]] == ["SCHEDULE_OK"]
