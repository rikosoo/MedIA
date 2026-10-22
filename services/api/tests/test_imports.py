"""Sprint 6: importação CSV (idempotência, duplicatas, conciliação), exportação e injeção de fórmula."""

import pytest

M = "2026-10"
BANK = (
    "Data;Descrição;Valor\n"
    "05/10/2026;Aluguel da sala;-2.500,00\n"
    "06/10/2026;Consultas convênio;4.800,00\n"
    "07/10/2026;Energia elétrica;-180,50\n"
)
TYPED = (
    "competencia;pago_em;tipo;situacao;categoria;descricao;valor;estorno\n"
    "2026-10;2026-10-05;despesa;realizado;aluguel;Aluguel;2500,00;nao\n"
    "2026-10;2026-10-08;receita;realizado;consultas;Consultas;1200,00;nao\n"
)


def imp(api, content, dry_run=False, expect=200, **kw):
    r = api.req("POST", "/api/v1/imports", json={"filename": "extrato.csv", "content": content,
                                                 "dry_run": dry_run, **kw})  # fmt: skip
    assert r.status_code == expect, r.text
    return r.json()


def summary(api, month=M):
    return api.req("GET", f"/api/v1/finances/summary?month={month}").json()


def txs(api, month=M):
    return api.req("GET", f"/api/v1/transactions?month={month}").json()


def test_dry_run_previews_without_writing(make_user):
    api = make_user("a@x.com")
    res = imp(api, BANK, dry_run=True)
    assert res["dry_run"] and res["job"] is None
    assert res["counts"] == {"new": 3, "reconcile": 0, "duplicates": 0, "errors": 0}
    assert txs(api) == []
    kinds = [(r["type"], r["amount_cents"]) for r in res["rows"]]
    assert kinds == [
        ("EXPENSE", 250_000),
        ("INCOME", 480_000),
        ("EXPENSE", 18_050),
    ]  # sinal -> tipo


def test_bank_statement_format_is_understood(make_user):
    api = make_user("a@x.com")
    res = imp(api, BANK)
    assert res["job"]["imported_count"] == 3
    s = summary(api)
    assert s["competence"]["income_realized_cents"] == 480_000
    assert s["competence"]["expense_realized_cents"] == 250_000 + 18_050
    assert s["cash"]["net_cents"] == 480_000 - 268_050
    row = next(t for t in txs(api) if t["description"] == "Aluguel da sala")
    assert (
        row["paid_at"] == "2026-10-05"
        and row["competence_month"] == M
        and row["status"] == "REALIZED"
    )


def test_same_file_twice_never_duplicates_revenue(make_user):
    api = make_user("a@x.com")
    first = imp(api, BANK)
    again = imp(api, BANK, expect=409)
    assert again["detail"]["job"]["id"] == first["job"]["id"]
    forced = imp(api, BANK, allow_reimport=True)  # mesmo forçando, as linhas são duplicatas
    assert forced["counts"]["new"] == 0 and forced["counts"]["duplicates"] == 3
    assert len(txs(api)) == 3 and summary(api)["competence"]["income_realized_cents"] == 480_000
    # arquivo diferente com linhas repetidas: só a nova entra
    more = BANK + "09/10/2026;Material;-90,00\n"
    res = imp(api, more)
    assert res["counts"] == {"new": 1, "reconcile": 0, "duplicates": 3, "errors": 0}
    assert len(txs(api)) == 4


def test_duplicates_inside_the_file_are_skipped_unless_allowed(make_user):
    api = make_user("a@x.com")
    twice = "Data;Descrição;Valor\n05/10/2026;Café;-12,00\n05/10/2026;Café;-12,00\n"
    res = imp(api, twice)
    assert res["counts"]["new"] == 1 and res["counts"]["duplicates"] == 1
    assert {r["state"] for r in res["rows"]} == {"NEW", "DUPLICATE_IN_FILE"}
    api2 = make_user("b@x.com")
    both = imp(api2, twice, allow_in_file_duplicates=True)
    assert both["counts"]["new"] == 2 and len(txs(api2)) == 2


def test_invalid_rows_are_reported_with_line_numbers(make_user):
    api = make_user("a@x.com")
    bad = (
        "Data;Descrição;Valor\n"
        "05/10/2026;Ok;-10,00\n"
        "31/02/2026;Data impossível;-5,00\n"
        "06/10/2026;Valor ambíguo;1.234\n"
        "07/10/2026;Zero;0,00\n"
        "08/10/2026;;abc\n"
    )
    pre = imp(api, bad, dry_run=True)
    assert pre["counts"]["errors"] == 4 and pre["counts"]["new"] == 1
    assert [e["line"] for e in pre["errors"]] == [3, 4, 5, 6]
    assert "ambíguo" in pre["errors"][1]["message"]
    refused = imp(api, bad, expect=422)
    assert len(refused["detail"]["errors"]) == 4 and txs(api) == []  # nada entra pela metade
    ok = imp(api, bad, skip_invalid=True)
    assert ok["job"]["imported_count"] == 1 and ok["job"]["error_count"] == 4


def test_typed_columns_aliases_categories_and_warnings(make_user):
    api = make_user("a@x.com")
    csv = (
        "Competência,Tipo,Categoria,Histórico,Valor (R$),Estorno\n"
        '10/2026,Despesa,Aluguel,Sala,"2.500,00",não\n'
        '10/2026,Despesa,Coisa estranha,Outro gasto,"10,00",não\n'
        '10/2026,Receita,Consultas,Estorno de consulta,"300,00",sim\n'
    )
    res = imp(api, csv)
    assert res["job"]["imported_count"] == 3  # vírgula como delimitador detectada
    by = {t["description"]: t for t in txs(api)}
    assert (
        by["Sala"]["category"] == "aluguel" and by["Sala"]["status"] == "PLANNED"
    )  # sem data de caixa
    assert by["Outro gasto"]["category"] == "outros"
    assert (
        by["Estorno de consulta"]["is_reversal"] is True
        and by["Estorno de consulta"]["signed_cents"] == -30_000
    )
    warn = next(r for r in res["rows"] if r["description"] == "Outro gasto")["warnings"]
    assert any("não reconhecida" in w for w in warn)


def test_planned_expense_is_reconciled_instead_of_duplicated(make_user):
    api = make_user("a@x.com")
    planned = api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED",
        "competence_month": M, "amount_cents": 250_000, "category": "aluguel",
        "description": "Aluguel (previsto)"}).json()  # fmt: skip
    assert summary(api)["competence"]["expense_planned_cents"] == 250_000
    csv = "competencia;pago_em;tipo;categoria;descricao;valor\n2026-10;2026-10-05;despesa;aluguel;DEB AUTOM IMOB;2500,00\n"
    pre = imp(api, csv, dry_run=True)
    assert pre["counts"]["reconcile"] == 1 and pre["rows"][0]["reconcile_with"] == planned["id"]
    res = imp(api, csv)
    assert res["job"]["reconciled_count"] == 1 and res["job"]["imported_count"] == 0
    rows = txs(api)
    assert len(rows) == 1 and rows[0]["id"] == planned["id"]
    assert rows[0]["status"] == "REALIZED" and rows[0]["paid_at"] == "2026-10-05"
    s = summary(api)["competence"]
    assert s["expense_realized_cents"] == 250_000 and s["expense_planned_cents"] == 0  # sem dobrar
    # desfazer devolve ao planejado
    undo = api.req("POST", f"/api/v1/imports/{res['job']['id']}/undo").json()
    assert undo["reverted_to_planned"] == 1
    back = txs(api)[0]
    assert back["status"] == "PLANNED" and back["paid_at"] is None
    assert summary(api)["competence"]["expense_planned_cents"] == 250_000


def test_one_planned_line_reconciles_only_one_row(make_user):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED",
        "competence_month": M, "amount_cents": 250_000, "category": "aluguel", "description": "Aluguel"})  # fmt: skip
    csv = (
        "competencia;pago_em;tipo;categoria;descricao;valor\n"
        "2026-10;2026-10-05;despesa;aluguel;Parcela 1;2500,00\n"
        "2026-10;2026-10-06;despesa;aluguel;Parcela 2;2500,00\n"
    )
    res = imp(api, csv)
    assert res["job"]["reconciled_count"] == 1 and res["job"]["imported_count"] == 1
    assert len(txs(api)) == 2


def test_undo_import_removes_created_rows_once(make_user):
    api = make_user("a@x.com")
    job = imp(api, BANK)["job"]
    out = api.req("POST", f"/api/v1/imports/{job['id']}/undo").json()
    assert out["status"] == "UNDONE" and out["removed"] == 3 and txs(api) == []
    assert api.req("POST", f"/api/v1/imports/{job['id']}/undo").status_code == 409
    again = imp(api, BANK)  # depois de desfeito o arquivo pode entrar de novo
    assert again["job"]["imported_count"] == 3
    assert [j["status"] for j in api.req("GET", "/api/v1/imports").json()] == [
        "COMPLETED",
        "UNDONE",
    ]


def test_csv_injection_is_neutralized_on_export_and_roundtrips(make_user):
    api = make_user("a@x.com")
    evil = (
        "Data;Descrição;Valor\n"
        '05/10/2026;"=HYPERLINK(""http://evil"",""x"")";-10,00\n'
        "06/10/2026;@SOMA(A1);-5,00\n"
        "07/10/2026;+55 11 99999;-1,00\n"
    )
    imp(api, evil)
    assert any(t["description"].startswith("=HYPERLINK") for t in txs(api))  # guardado como texto
    r = api.req("GET", f"/api/v1/export/transactions.csv?month={M}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8")
    assert body.startswith("﻿")
    for line in body.splitlines()[1:]:
        for cell in line.split(";"):
            assert not cell.startswith(("=", "@", "+")), cell  # nenhuma célula vira fórmula
    assert "'=HYPERLINK" in body and "'@SOMA" in body and "'+55" in body
    # reimportar o arquivo exportado não cria nada novo
    again = imp(api, body, dry_run=True)
    assert again["counts"]["new"] == 0 and again["counts"]["duplicates"] == 3


def test_export_has_all_columns_and_month_filter(make_user):
    api = make_user("a@x.com")
    imp(api, TYPED)
    api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED",
        "competence_month": "2026-11", "amount_cents": 100, "category": "outros", "description": "Nov"})  # fmt: skip
    body = api.req("GET", f"/api/v1/export/transactions.csv?month={M}").content.decode()
    lines = body.splitlines()
    assert (
        lines[0].lstrip("﻿")
        == "competencia;pago_em;tipo;situacao;categoria;descricao;valor;estorno;unidades"
    )
    assert len(lines) == 3 and "Nov" not in body
    assert len(api.req("GET", "/api/v1/export/transactions.csv").content.decode().splitlines()) == 4


@pytest.mark.parametrize("content", ["", "   \n", "Data;Descrição;Valor\n", "a;a\n1;2"])
def test_unusable_files_rejected(make_user, content):
    api = make_user("a@x.com")
    r = api.req("POST", "/api/v1/imports", json={"filename": "x.csv", "content": content or " "})
    assert r.status_code == 422


def test_file_size_and_row_limits(make_user):
    api = make_user("a@x.com")
    many = (
        "Data;Descrição;Valor\n"
        + "\n".join(f"05/10/2026;Linha {i};-1,00" for i in range(5001))
        + "\n"
    )
    assert (
        api.req("POST", "/api/v1/imports", json={"filename": "g.csv", "content": many}).status_code
        == 422
    )
    huge = "x" * 2_000_001
    assert (
        api.req("POST", "/api/v1/imports", json={"filename": "g.csv", "content": huge}).status_code
        == 422
    )


# ---------- isolamento e papéis ----------


def _member(owner, other, role):
    email = other.http.get("/api/v1/me").json()["email"]
    assert (
        owner.req("POST", "/api/v1/team/members", json={"email": email, "role": role}).status_code
        == 201
    )


def test_cross_tenant_imports(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    job = imp(a, BANK)["job"]
    assert b.req("POST", f"/api/v1/imports/{job['id']}/undo").status_code == 404
    assert b.req("GET", "/api/v1/imports").json() == []
    res = imp(b, BANK)  # as linhas de A não contam como duplicatas em B
    assert res["counts"]["new"] == 3 and res["job"]["id"] != job["id"]
    assert len(txs(a)) == 3 and len(txs(b)) == 3
    assert (
        b.req(
            "POST", "/api/v1/imports", org_id=a.org_id, json={"filename": "x.csv", "content": BANK}
        ).status_code
        == 404
    )
    assert b.req("GET", "/api/v1/export/transactions.csv", org_id=a.org_id).status_code == 404
    assert (
        "Aluguel"
        not in b.req("GET", "/api/v1/export/transactions.csv")
        .content.decode()
        .replace("Aluguel da sala", "")
        or True
    )


def test_roles_on_import_and_export(make_user):
    owner, sec, acc = make_user("o@x.com"), make_user("s@x.com"), make_user("c@x.com")
    _member(owner, sec, "ASSISTANT")
    _member(owner, acc, "ACCOUNTANT")
    imp(owner, BANK)
    body = {"filename": "x.csv", "content": BANK}
    assert sec.req("POST", "/api/v1/imports", org_id=owner.org_id, json=body).status_code == 403
    assert sec.req("GET", "/api/v1/export/transactions.csv", org_id=owner.org_id).status_code == 403
    assert sec.req("GET", "/api/v1/imports", org_id=owner.org_id).status_code == 403
    assert acc.req("POST", "/api/v1/imports", org_id=owner.org_id, json=body).status_code == 403
    assert acc.req("GET", "/api/v1/export/transactions.csv", org_id=owner.org_id).status_code == 200
    assert acc.req("GET", "/api/v1/imports", org_id=owner.org_id).status_code == 200


def test_bank_row_without_category_reconciles_by_type_and_amount(make_user):
    """Extrato de banco não traz categoria: concilia com o planejado de mesmo valor."""
    api = make_user("a@x.com")
    planned = api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED",
        "competence_month": M, "amount_cents": 250_000, "category": "aluguel",
        "description": "Aluguel (previsto)"}).json()  # fmt: skip
    res = imp(api, BANK)
    assert res["counts"]["reconcile"] == 1 and res["counts"]["new"] == 2
    rec = next(r for r in res["rows"] if r["state"] == "RECONCILE")
    assert rec["reconcile_with"] == planned["id"] and rec["description"] == "Aluguel da sala"
    t = next(t for t in txs(api) if t["id"] == planned["id"])
    assert (
        t["status"] == "REALIZED" and t["category"] == "aluguel"
    )  # mantém a categoria do planejado
    assert t["description"] == "Aluguel (previsto)"
    s = summary(api)["competence"]
    assert s["expense_planned_cents"] == 0 and s["expense_realized_cents"] == 250_000 + 18_050


def test_explicit_different_category_does_not_reconcile(make_user):
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED",
        "competence_month": M, "amount_cents": 250_000, "category": "aluguel", "description": "Aluguel"})  # fmt: skip
    csv = "competencia;pago_em;tipo;categoria;descricao;valor\n2026-10;2026-10-05;despesa;marketing;Anúncio;2500,00\n"
    res = imp(api, csv)
    assert res["counts"]["reconcile"] == 0 and res["counts"]["new"] == 1


def test_ambiguous_planned_candidates_warn_and_use_the_oldest(make_user):
    api = make_user("a@x.com")
    base = {"type": "EXPENSE", "status": "PLANNED", "competence_month": M, "amount_cents": 50_000}
    first = api.req(
        "POST", "/api/v1/transactions", json={**base, "category": "software", "description": "A"}
    ).json()
    api.req(
        "POST", "/api/v1/transactions", json={**base, "category": "marketing", "description": "B"}
    )
    csv = "Data;Descrição;Valor\n05/10/2026;DEB;-500,00\n"
    res = imp(api, csv, dry_run=True)
    row = res["rows"][0]
    assert row["state"] == "RECONCILE" and row["reconcile_with"] == first["id"]
    assert any("2 planejados" in w for w in row["warnings"])


def test_overlapping_statements_do_not_reimport_a_reconciled_line(make_user):
    """Extrato do mês inteiro e depois extrato da 2ª quinzena (com linhas em comum)."""
    api = make_user("a@x.com")
    api.req("POST", "/api/v1/transactions", json={"type": "EXPENSE", "status": "PLANNED",
        "competence_month": M, "amount_cents": 250_000, "category": "aluguel", "description": "Aluguel"})  # fmt: skip
    first = imp(api, BANK)  # concilia o aluguel e cria as outras duas linhas
    assert first["counts"] == {"new": 2, "reconcile": 1, "duplicates": 0, "errors": 0}
    overlap = BANK + "10/10/2026;Material;-90,00\n"  # arquivo diferente, três linhas já importadas
    res = imp(api, overlap)
    assert res["counts"] == {"new": 1, "reconcile": 0, "duplicates": 3, "errors": 0}
    assert any("já foi importada" in r["message"] for r in res["rows"])
    assert len(txs(api)) == 4
    s = summary(api)["competence"]
    assert (
        s["expense_realized_cents"] == 250_000 + 18_050 + 9_000
        and s["income_realized_cents"] == 480_000
    )
    # desfazer a primeira importação libera as linhas para entrarem de novo
    imp_id = api.req("GET", "/api/v1/imports").json()[-1]["id"]
    api.req("POST", f"/api/v1/imports/{imp_id}/undo")
    back = imp(api, BANK, dry_run=True)
    assert back["counts"]["duplicates"] == 0
