"""Sprint 9: consultor IA que só explica números do motor, sem alterar dados."""

import re

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AiConversation,
    AiMessage,
    AuditLog,
    IncomePlan,
    MonthlyGoal,
    Transaction,
    WorkSource,
)  # fmt: skip
from app.modules.assistant.engine import (
    Facts,
    grounded,
    intent_of,
    parse_count,
    parse_money_cents,
    parse_months,
    redact,
)  # fmt: skip
from tests.conftest import SHIFT
from tests.test_clinic import _setup
from tests.test_goals import goal

M = "2026-10"


def plan(api, kind, sid, units, certainty="GUARANTEED", month=None, **kw):
    r = api.req("PUT", "/api/v1/income-plans", json={"month": month, "source_type": kind,
        "source_id": sid, "units": units, "certainty": certainty, **kw})  # fmt: skip
    assert r.status_code == 200, r.text


def scenario(api):
    w = api.req("POST", "/api/v1/work-sources", json=SHIFT).json()
    _, svc = _setup(api)
    plan(api, "WORK", w["id"], 4)
    plan(api, "SERVICE", svc["id"], 20, "PROBABLE", tax_rate_bps=1000)
    goal(api, 3_000_000, month=M)
    return w, svc


def ask(api, message, expect=200, **kw):
    r = api.req("POST", "/api/v1/assistant/chat", json={"message": message, "month": M, **kw})
    assert r.status_code == expect, r.text
    return r.json()


def counts(app):
    with app.state.session_factory() as s:
        return [
            s.scalar(select(func.count()).select_from(t))
            for t in (Transaction, IncomePlan, MonthlyGoal, WorkSource)
        ]


# ---------- interpretação ----------


@pytest.mark.parametrize("text,cents", [
    ("Esse mês preciso ganhar R$ 50 mil; onde mexo?", 5_000_000),
    ("preciso de R$ 12.500,50", 1_250_050),
    ("quero 50.000", 5_000_000),
    ("8 consultas a mais", None),
    ("15 dias de férias", None),
])  # fmt: skip
def test_parse_money(text, cents):
    assert parse_money_cents(text) == cents


def test_parse_counts_and_months():
    assert parse_count("é melhor um plantão ou 8 consultas?", "consultas?") == 8
    from datetime import date

    assert parse_months("o que mudou de setembro para outubro", date(2026, 10, 1)) == [
        date(2026, 9, 1), date(2026, 10, 1)]  # fmt: skip
    assert parse_months("de dezembro", date(2026, 10, 1)) == [date(2025, 12, 1)]


@pytest.mark.parametrize("text,intent", [
    ("Esse mês preciso ganhar R$ 50 mil; onde mexo?", "GAP"),
    ("É melhor um plantão ou 8 consultas a mais?", "COMPARE"),
    ("Se a secretária tirar 15 dias de férias, o que acontece?", "LEAVE"),
    ("Qual é meu trabalho com maior renda por hora comprometida?", "HOURLY"),
    ("Quanto perco com retornos gratuitos?", "RETURNS"),
    ("O que mudou de setembro para outubro?", "CHANGE"),
    ("Explique os números do mês", "OVERVIEW"),
    ("qual a capital da França", "UNKNOWN"),
])  # fmt: skip
def test_intents(text, intent):
    assert intent_of(text) == intent


def test_redaction_removes_identifiers():
    t = redact(
        "meu cpf 123.456.789-09 e-mail a.b@x.com tel (11) 98888-7777 cnpj 12.345.678/0001-90"
    )
    assert not re.search(r"\d{3}\.\d{3}|@|9888", t)


def test_grounding_check_rejects_invented_numbers():
    f = Facts()
    f.add("a", "x", 123_456, "money", "t")
    assert grounded("Você tem R$ 1.234,56", f)
    assert not grounded("Você tem R$ 9.999,00", f)


# ---------- respostas ----------


def test_gap_answer_uses_engine_numbers(make_user):
    api = make_user("a@x.com")
    scenario(api)
    out = ask(api, "Esse mês preciso ganhar R$ 50 mil; onde mexo?")
    a = out["message"]["answer"]
    dash = api.req("GET", f"/api/v1/dashboard?month={M}").json()
    facts = {f["key"]: f["value"] for f in a["facts"]}
    assert a["intent"] == "GAP" and a["grounded"] is True
    assert facts["projected"] == dash["projection"]["value_cents"]
    assert facts["target"] == 5_000_000
    assert facts["gap"] == 5_000_000 - facts["projected"]
    assert a["scenarios"] and a["actions"] and a["simulation_link"] == "/app/optimizer"
    assert a["risks"] and "estimativa" in " ".join(a["assumptions"]).lower()


def test_redo_with_another_target(make_user):
    api = make_user("a@x.com")
    scenario(api)
    a = ask(api, "Onde mexo para bater a meta?", target_cents=4_000_000)["message"]["answer"]
    assert {f["key"]: f["value"] for f in a["facts"]}["target"] == 4_000_000


def test_gap_zero_when_target_is_already_met(make_user):
    api = make_user("a@x.com")
    scenario(api)
    a = ask(api, "preciso ganhar R$ 1.000")["message"]["answer"]
    assert "já é atingida" in a["summary"]


def test_gap_without_goal_asks_for_one(make_user):
    api = make_user("a@x.com")
    a = ask(api, "onde mexo para bater a meta?")["message"]["answer"]
    assert "meta" in a["summary"] and a["actions"][0]["route"] == "/app/goals"


def test_compare_shift_vs_consultations(make_user):
    api = make_user("a@x.com")
    scenario(api)
    a = ask(api, "É melhor um plantão ou 8 consultas a mais?")["message"]["answer"]
    f = {x["key"]: x["value"] for x in a["facts"]}
    assert a["intent"] == "COMPARE" and a["grounded"] is True
    assert f["n_consults"] == 8 and f["shift_net"] == 170_000
    assert f["consult_net"] > 0 and "shift_hour" in f and len(a["scenarios"]) == 2


def test_hourly_ranking_is_sorted(make_user):
    api = make_user("a@x.com")
    scenario(api)
    a = ask(api, "Qual é meu trabalho com maior renda por hora comprometida?")["message"]["answer"]
    vals = [x["value"] for x in a["facts"] if x["key"].startswith("hour_")]
    assert len(vals) == 2 and vals == sorted(vals, reverse=True) and a["grounded"]


def test_returns_free_has_opportunity_cost_only(make_user):
    api = make_user("a@x.com")
    scenario(api)
    a = ask(api, "Quanto perco com retornos gratuitos?")["message"]["answer"]
    keys = {x["key"] for x in a["facts"]}
    assert "ret_loss_1" in keys and "ret_rev_1" not in keys
    assert "hipótese" in " ".join(a["risks"]) and a["grounded"]


def test_change_between_months(make_user):
    api = make_user("a@x.com")
    scenario(api)
    a = ask(api, "O que mudou de setembro para outubro?")["message"]["answer"]
    f = {x["key"]: x["value"] for x in a["facts"]}
    assert a["month"] == "2026-10" and f["projected_delta"] == f["projected_1"] - f["projected_0"]
    assert a["grounded"]


def test_secretary_leave_uses_acquisition_data(make_user):
    api = make_user("a@x.com")
    scenario(api)
    ch = api.req(
        "POST", "/api/v1/acquisition/channels", json={"name": "Google", "channel_type": "GOOGLE"}
    ).json()
    api.req("PUT", "/api/v1/acquisition/metrics", json={"month": M, "channel_id": ch["id"],
        "leads_count": 100, "responded_in_time_count": 90, "bookings_count": 20,
        "attended_count": 18, "spend_cents": 0})  # fmt: skip
    a = ask(api, "Se a secretária tirar 15 dias de férias, o que acontece?")["message"]["answer"]
    f = {x["key"]: x["value"] for x in a["facts"]}
    assert f["coverage_lost"] == 4838 and f["lost_bookings"] == 9 and a["grounded"]


def test_physician_leave_cuts_net_proportionally(make_user):
    api = make_user("a@x.com")
    scenario(api)
    a = ask(api, "E se eu tirar 10 dias de férias?")["message"]["answer"]
    f = {x["key"]: x["value"] for x in a["facts"]}
    assert f["lost_days"] == 7 and f["work_days"] == 22 and f["net_cut"] > 0
    assert f["net_cut"] == f["net_planned"] * 7 // 22


def test_leave_without_days_asks(make_user):
    api = make_user("a@x.com")
    a = ask(api, "e se a secretária tirar férias?")["message"]["answer"]
    assert "quantos dias" in a["summary"]


def test_overview_and_unknown(make_user):
    api = make_user("a@x.com")
    scenario(api)
    ov = ask(api, "Explique os números do mês")["message"]["answer"]
    assert (
        ov["intent"] == "OVERVIEW"
        and ov["grounded"]
        and any(f["key"] == "gap" for f in ov["facts"])
    )
    unk = ask(api, "qual a capital da França")["message"]["answer"]
    assert unk["intent"] == "UNKNOWN" and unk["facts"] == [] and "Não invento" in unk["summary"]


def test_every_numeric_token_in_answers_is_grounded(make_user):
    api = make_user("a@x.com")
    scenario(api)
    for q in [
        "preciso ganhar R$ 50 mil",
        "plantão ou 8 consultas",
        "férias de 15 dias da secretária",
        "maior renda por hora",
        "perco com retornos gratuitos",
        "o que mudou de setembro para outubro",
    ]:
        assert ask(api, q)["message"]["answer"]["grounded"] is True, q


# ---------- somente leitura, histórico e segurança ----------


def test_chat_never_changes_financial_data(make_user, app):
    api = make_user("a@x.com")
    scenario(api)
    before = counts(app)
    for q in [
        "preciso ganhar R$ 50 mil",
        "plantão ou 8 consultas",
        "o que mudou de setembro para outubro",
    ]:
        ask(api, q)
    assert counts(app) == before


def test_history_save_and_delete(make_user):
    api = make_user("a@x.com")
    scenario(api)
    out = ask(api, "preciso ganhar R$ 50 mil")
    cid, mid = out["conversation_id"], out["message"]["id"]
    out2 = ask(api, "maior renda por hora", conversation_id=cid)
    assert out2["conversation_id"] == cid
    conv = api.req("GET", f"/api/v1/assistant/conversations/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["USER", "ASSISTANT", "USER", "ASSISTANT"]
    assert (
        api.req("POST", f"/api/v1/assistant/messages/{mid}/save", json={}).json()["saved"] is True
    )
    assert len(api.req("GET", "/api/v1/assistant/saved").json()) == 1
    assert api.req("DELETE", f"/api/v1/assistant/conversations/{cid}").status_code == 204
    assert api.req("GET", f"/api/v1/assistant/conversations/{cid}").status_code == 404
    assert api.req("GET", "/api/v1/assistant/saved").json() == []


def test_stored_history_is_redacted_and_audit_has_no_values(make_user, app):
    api = make_user("a@x.com")
    scenario(api)
    ask(api, "meu cpf 123.456.789-09, preciso ganhar R$ 50 mil")
    with app.state.session_factory() as s:
        user_msg = s.scalar(select(AiMessage).where(AiMessage.role == "USER"))
        assert "123.456" not in user_msg.content_redacted
        logs = list(s.scalars(select(AuditLog).where(AuditLog.action == "assistant.chat")))
        assert logs and all(set(lg.metadata_json) <= {"intent", "grounded"} for lg in logs)
        assert s.scalar(select(func.count()).select_from(AiConversation)) == 1


def test_conversations_are_private_to_the_owner(make_user):
    a, b = make_user("a@x.com"), make_user("b@x.com")
    scenario(a)
    out = ask(a, "preciso ganhar R$ 50 mil")
    cid = out["conversation_id"]
    assert b.req("GET", f"/api/v1/assistant/conversations/{cid}").status_code == 404
    assert b.req("GET", f"/api/v1/assistant/conversations/{cid}", org_id=a.org_id).status_code in (
        403,
        404,
    )
    assert b.req(
        "POST",
        "/api/v1/assistant/chat",
        org_id=a.org_id,
        json={"message": "olá teste", "conversation_id": cid},
    ).status_code in (403, 404)
    assert b.req("GET", "/api/v1/assistant/conversations").json() == []


def test_roles_without_goal_permission_are_blocked(make_user):
    owner, sec = make_user("o@x.com"), make_user("s@x.com")
    email = sec.http.get("/api/v1/me").json()["email"]
    owner.req("POST", "/api/v1/team/members", json={"email": email, "role": "ASSISTANT"})
    r = sec.req(
        "POST",
        "/api/v1/assistant/chat",
        org_id=owner.org_id,
        json={"message": "preciso ganhar R$ 50 mil"},
    )
    assert r.status_code == 403
    assert sec.req("GET", "/api/v1/assistant/suggestions", org_id=owner.org_id).status_code == 403


def test_validation_and_auth(make_user, app):
    api = make_user("a@x.com")
    assert api.req("POST", "/api/v1/assistant/chat", json={"message": "x"}).status_code == 422
    assert api.req("POST", "/api/v1/assistant/chat", json={"message": "y" * 501}).status_code == 422
    assert (
        api.req(
            "POST", "/api/v1/assistant/chat", json={"message": "ok teste", "month": "2026-13"}
        ).status_code
        == 422
    )
    from fastapi.testclient import TestClient

    assert TestClient(app).post("/api/v1/assistant/chat", json={"message": "oi"}).status_code in (
        401,
        403,
    )


def test_daily_limit(make_user, monkeypatch):
    from app.modules.assistant import router

    monkeypatch.setattr(router, "DAILY_LIMIT", 2)
    api = make_user("a@x.com")
    ask(api, "explique os números")
    ask(api, "explique os números")
    ask(api, "explique os números", expect=429)


def test_suggestions_match_the_prd_questions(make_user):
    api = make_user("a@x.com")
    s = api.req("GET", "/api/v1/assistant/suggestions").json()
    assert len(s) >= 6 and all(intent_of(q) != "UNKNOWN" for q in s)
