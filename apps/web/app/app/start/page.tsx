"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents } from "@/lib/money";
import { formatMinutes } from "@/lib/time";
import { useSession } from "../session";

type Source = { id: string; kind: string; name: string; units: number; net_cents: number; net_per_unit_cents: number; minutes_per_unit: number; tax_source: string };
type Cost = { id: string; label: string; amount_cents: number };
type Idea = { kind: string; title: string; detail: string; closes_gap: boolean; extra_minutes?: number };
type Summary = {
  month: string; goal: { id: string; target_cents: number } | null; sources: Source[]; income_net_cents: number;
  costs: { total_cents: number; recurring: Cost[]; other_cents: number };
  result_cents: number; gap_cents: number | null; status: string; ideas: Idea[]; notes: string[];
};
type Ent = { limits: Record<string, number | null>; plan_name: string; simple: boolean };

const KINDS: Record<string, { label: string; value: string; qty: string; time: string; hours: string; workType?: string }> = {
  CONSULT: { label: "Consulta particular", value: "Valor da consulta (R$)", qty: "Consultas por mês", time: "Duração de cada uma (horas)", hours: "0,5" },
  SHIFT: { label: "Plantão", value: "Valor de cada plantão (R$)", qty: "Plantões por mês", time: "Horas de cada plantão", hours: "12", workType: "SHIFT" },
  ON_CALL: { label: "Sobreaviso", value: "Valor de cada sobreaviso (R$)", qty: "Sobreavisos por mês", time: "Horas de disponibilidade", hours: "12", workType: "ON_CALL" },
  EMPLOYMENT: { label: "CLT / salário fixo", value: "Salário mensal (R$)", qty: "", time: "Horas trabalhadas por mês", hours: "160", workType: "EMPLOYMENT" },
  OTHER: { label: "Outra renda", value: "Valor de cada (R$)", qty: "Quantidade por mês", time: "Horas de cada uma", hours: "1", workType: "OTHER" },
};
const STEPS = ["Meta", "Rendas", "Custos", "Ideias"];
const COST_CODES = ["aluguel", "condominio", "utilidades", "secretaria", "equipamentos", "manutencao", "contabilidade", "marketing", "software", "material", "outros"];
const monthNow = () => new Date().toISOString().slice(0, 7);
const monthLabel = (m: string) => new Date(`${m}-15T12:00:00`).toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
const toHours = (t: string) => Number(t.trim().replace(",", "."));

export default function StartPage() {
  const { me, org } = useSession();
  const month = monthNow();
  const [s, setS] = useState<Summary | null>(null);
  const [ent, setEnt] = useState<Ent | null>(null);
  const [cats, setCats] = useState<Record<string, string>>({});
  const [kind, setKind] = useState("CONSULT");
  const [step, setStep] = useState(1);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  const load = useCallback(() => {
    call<Summary>(`/api/v1/quick/summary?month=${month}`).then(setS).catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? "Você não tem permissão para ver metas e rendas." : e.message) : "Falha ao carregar"));
  }, [call, month]);
  useEffect(load, [load]);
  useEffect(() => {
    call<Ent>("/api/v1/billing/entitlements").then(setEnt).catch(() => setEnt(null));
    call<{ expense: Record<string, string> }>("/api/v1/finance/categories").then((c) => setCats(c.expense)).catch(() => setCats({}));
  }, [call]);

  async function run(fn: () => Promise<unknown>, form?: HTMLFormElement) {
    setBusy(true); setError(null);
    try { await fn(); form?.reset(); setAdding(false); load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Não foi possível salvar"); } finally { setBusy(false); }
  }

  function saveGoal(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const cents = parseBRLToCents(String(new FormData(form).get("goal") ?? ""));
    if (cents === null || cents <= 0) return setError("Meta inválida. Use o formato 50.000,00");
    void run(() => (s?.goal ? call(`/api/v1/goals/${s.goal.id}`, { method: "PATCH", body: { target_cents: cents } })
      : call("/api/v1/goals", { method: "POST", body: { month, metric_type: "PERSONAL_NET", target_cents: cents } })));
  }

  function addSource(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const cfg = KINDS[kind];
    const cents = parseBRLToCents(String(f.get("value") ?? ""));
    const hours = toHours(String(f.get("hours") ?? ""));
    const qty = kind === "EMPLOYMENT" ? 1 : Number(f.get("qty"));
    if (cents === null || cents <= 0) return setError("Valor inválido. Use o formato 2.000,00");
    if (!Number.isFinite(hours) || hours <= 0) return setError("Informe as horas (ex.: 12 ou 0,5).");
    if (!Number.isInteger(qty) || qty < 1) return setError("Informe a quantidade por mês (1 ou mais).");
    const name = String(f.get("name") || cfg.label).trim();
    const minutes = Math.round(hours * 60);
    void run(async () => {
      if (kind === "CONSULT") {
        const svc = await call<{ id: string }>("/api/v1/clinic-services", { method: "POST", body: { name, price_cents: cents, duration_minutes: minutes } });
        try { await call("/api/v1/income-plans", { method: "PUT", body: { month: null, source_type: "SERVICE", source_id: svc.id, units: qty, certainty: "PROBABLE" } }); }
        catch (err) { await call(`/api/v1/clinic-services/${svc.id}`, { method: "DELETE" }); throw err; }
      } else {
        const regime = kind === "EMPLOYMENT" ? "CLT" : String(f.get("regime") || "PJ");
        const w = await call<{ id: string }>("/api/v1/work-sources", { method: "POST", body: { name, work_type: cfg.workType, regime, income: { gross_cents: cents, unit_type: kind === "EMPLOYMENT" ? "MONTHLY" : "PER_EVENT", duration_minutes: minutes, commitment_minutes: minutes } } });
        try { await call("/api/v1/income-plans", { method: "PUT", body: { month: null, source_type: "WORK", source_id: w.id, units: qty, certainty: kind === "EMPLOYMENT" ? "GUARANTEED" : "PROBABLE" } }); }
        catch (err) { await call(`/api/v1/work-sources/${w.id}`, { method: "DELETE" }); throw err; }
      }
    }, form);
  }

  function removeSource(x: Source) {
    void run(() => call(x.kind === "SERVICE" ? `/api/v1/clinic-services/${x.id}` : `/api/v1/work-sources/${x.id}`, { method: "DELETE" }));
  }

  function addCost(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const cents = parseBRLToCents(String(f.get("amount") ?? ""));
    const category = String(f.get("category"));
    if (cents === null || cents <= 0) return setError("Valor inválido. Use o formato 3.000,00");
    void run(() => call("/api/v1/recurrences", { method: "POST", body: { description: cats[category] ?? category, category, amount_cents: cents, frequency: "MONTHLY", start_date: `${month}-01` } }), form);
  }

  const cfg = KINDS[kind];
  const limits = ent?.limits;
  const goNext = () => { setError(null); setAdding(false); setStep((n) => Math.min(4, n + 1)); window.scrollTo?.({ top: 0 }); };
  const goBack = () => { setError(null); setAdding(false); setStep((n) => Math.max(1, n - 1)); window.scrollTo?.({ top: 0 }); };
  const goTo = (n: number) => { setError(null); setAdding(false); setStep(n); };

  return (
    <div className="narrow wizard">
      <h1>Plano direto</h1>
      <p className="muted">Quanto você ganha em {monthLabel(month)} e o que fazer para bater a meta.</p>

      <ol className="steps" aria-label="Passos">
        {STEPS.map((label, k) => (
          <li key={label}>
            <button type="button" className="step" aria-current={step === k + 1 ? "step" : undefined} data-done={step > k + 1} onClick={() => goTo(k + 1)}>
              <span className="n">{k + 1}</span><span className="t">{label}</span>
            </button>
          </li>
        ))}
      </ol>

      {s && s.goal && step < 4 && (
        <p className="gapbar" role="status">
          {s.status === "MET" ? <>Meta atingida · sobra <strong>{formatBRL(s.result_cents)}</strong></>
            : s.status === "SHORT" ? <>Faltam <strong>{formatBRL(s.gap_cents ?? 0)}</strong> para a meta</>
            : <>Meta de <strong>{formatBRL(s.goal.target_cents)}</strong></>}
        </p>
      )}
      {error && <p role="alert" className="error">{error}{/limite|plano/i.test(error) && <> <Link href="/app/billing">Ver planos</Link></>}</p>}

      {step === 1 && (
        <section className="card" aria-labelledby="p1">
          <h2 id="p1">Quanto você quer ganhar líquido?</h2>
          <form onSubmit={(e) => { saveGoal(e); }}>
            <label htmlFor="goal">Meta do mês (R$)</label>
            <input id="goal" name="goal" inputMode="decimal" placeholder="50.000,00" defaultValue={s?.goal ? (s.goal.target_cents / 100).toLocaleString("pt-BR", { minimumFractionDigits: 2 }) : ""} key={s?.goal?.target_cents ?? "none"} />
            <button type="submit" className="secondary" disabled={busy}>{s?.goal ? "Atualizar meta" : "Salvar meta"}</button>
          </form>
        </section>
      )}

      {step === 2 && (
        <section aria-labelledby="p2">
          <h2 id="p2">De onde vem sua renda?</h2>
          {s && s.sources.map((x) => (
            <article key={x.id} className="item card">
              <div><strong>{x.name}</strong><div className="muted"><small>{x.units} por mês</small></div></div>
              <div className="amt">{formatBRL(x.net_cents)}<small className="muted"> líquidos</small></div>
              <button type="button" className="secondary" disabled={busy} aria-label={`Remover ${x.name}`} onClick={() => removeSource(x)}>Remover</button>
            </article>
          ))}
          {s && s.sources.length === 0 && !adding && <p className="muted">Nenhuma renda ainda. Toque em um tipo para começar.</p>}
          {!adding ? (
            <div className="chips" role="group" aria-label="Adicionar renda">
              {Object.entries(KINDS).map(([k, v]) => <button key={k} type="button" className="chip" onClick={() => { setKind(k); setAdding(true); }}>+ {v.label}</button>)}
            </div>
          ) : (
            <form className="card" onSubmit={addSource} aria-label={cfg.label}>
              <h3>{cfg.label}</h3>
              <div className="grid2" key={kind}>
                <div><label htmlFor="value">{cfg.value}</label><input id="value" name="value" inputMode="decimal" required placeholder="2.000,00" autoFocus /></div>
                {kind !== "EMPLOYMENT" && <div><label htmlFor="qty">{cfg.qty}</label><input id="qty" name="qty" type="number" inputMode="numeric" min="1" step="1" required defaultValue={kind === "CONSULT" ? 40 : 4} /></div>}
                <div><label htmlFor="hours">{cfg.time}</label><input id="hours" name="hours" inputMode="decimal" required defaultValue={cfg.hours} /></div>
                {kind !== "CONSULT" && kind !== "EMPLOYMENT" && (
                  <div><label htmlFor="regime">Você recebe como</label><select id="regime" name="regime" defaultValue="PJ"><option value="PJ">PJ (empresa)</option><option value="PF">PF (pessoa física)</option></select></div>
                )}
                <div><label htmlFor="name">Nome (opcional)</label><input id="name" name="name" placeholder={cfg.label} maxLength={200} /></div>
              </div>
              <div className="row">
                <button type="submit" disabled={busy}>{busy ? "Salvando…" : "Adicionar"}</button>
                <button type="button" className="secondary" onClick={() => setAdding(false)}>Cancelar</button>
              </div>
            </form>
          )}
          {limits?.works != null && <p className="muted"><small>Plano {ent?.plan_name}: até {limits.works} trabalhos. Consultas não entram nesse limite.</small></p>}
        </section>
      )}

      {step === 3 && (
        <section aria-labelledby="p3">
          <h2 id="p3">Quais são seus custos por mês?</h2>
          {s && s.costs.recurring.map((c) => (
            <article key={c.id} className="item card">
              <div><strong>{c.label}</strong></div>
              <div className="amt">{formatBRL(c.amount_cents)}</div>
              <button type="button" className="secondary" disabled={busy} aria-label={`Remover custo ${c.label}`} onClick={() => run(() => call(`/api/v1/recurrences/${c.id}`, { method: "DELETE" }))}>Remover</button>
            </article>
          ))}
          {s && s.costs.other_cents > 0 && <p className="muted">Mais {formatBRL(s.costs.other_cents)} em despesas lançadas no mês (veja em <Link href="/app/finances">Meus custos</Link>).</p>}
          <form className="card" onSubmit={addCost}>
            <div className="grid2">
              <div><label htmlFor="category">Custo</label><select id="category" name="category" defaultValue="aluguel">{COST_CODES.filter((c) => cats[c]).map((c) => <option key={c} value={c}>{cats[c]}</option>)}</select></div>
              <div><label htmlFor="amount">Valor por mês (R$)</label><input id="amount" name="amount" inputMode="decimal" required placeholder="3.000,00" /></div>
            </div>
            <button type="submit" className="secondary" disabled={busy}>+ Adicionar custo</button>
          </form>
        </section>
      )}

      {step === 4 && (
        <section aria-labelledby="res" aria-live="polite">
          <h2 id="res">Como bater a meta</h2>
          {!s ? <p aria-busy="true">Carregando…</p> : (
            <>
              <div className="kpis">
                <div className="card kpi"><div className="label">Renda líquida</div><div className="value">{formatBRL(s.income_net_cents)}</div></div>
                <div className="card kpi"><div className="label">Custos</div><div className="value">{formatBRL(s.costs.total_cents)}</div></div>
                <div className="card kpi"><div className="label">Sobra no mês</div><div className="value">{formatBRL(s.result_cents)}</div></div>
                {s.goal && <div className="card kpi"><div className="label">Meta</div><div className="value">{formatBRL(s.goal.target_cents)}</div></div>}
              </div>
              {s.status === "NO_GOAL" && <p>Defina sua meta no passo 1 para ver quanto falta.</p>}
              {s.status === "NO_SOURCES" && <p>Adicione sua primeira renda no passo 2 para calcular.</p>}
              {s.status === "MET" && (() => {
                const slack = s.result_cents - (s.goal?.target_cents ?? 0);
                return <p><strong>Meta atingida</strong> com o que você cadastrou{slack > 0 ? `, com folga de ${formatBRL(slack)}` : ""}. Priorize manter a reserva em vez de mais trabalho.</p>;
              })()}
              {s.status === "SHORT" && (
                <>
                  <p><strong>Faltam {formatBRL(s.gap_cents ?? 0)}</strong> para a sua meta. Ideias:</p>
                  {s.ideas.map((i, k) => (
                    <article key={k} className="idea card">
                      <strong>{i.title}</strong>
                      <p>{i.detail}{i.extra_minutes ? <> (cerca de {formatMinutes(i.extra_minutes)} a mais de trabalho)</> : null}</p>
                      <span className={i.closes_gap ? "tag ok" : "tag"}>{i.closes_gap ? "fecha a meta" : "ajuda, mas não fecha sozinho"}</span>
                    </article>
                  ))}
                  <p><Link href="/app/optimizer">Testar combinações na sua agenda (Otimizador)</Link></p>
                </>
              )}
              <ul className="muted">{s.notes.map((n) => <li key={n}><small>{n}</small></li>)}</ul>
            </>
          )}
        </section>
      )}

      <div className="wiznav">
        {step > 1 ? <button type="button" className="secondary" onClick={goBack}>Voltar</button> : <span />}
        {step < 4 && <button type="button" onClick={goNext}>{step === 3 ? "Ver ideias" : "Continuar"}</button>}
      </div>
    </div>
  );
}
