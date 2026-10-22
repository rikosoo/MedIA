"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents } from "@/lib/money";
import { formatMinutes } from "@/lib/time";
import { useSession } from "../session";

type Source = { source_type: string; source_id: string; name: string; eligible: boolean };
type Goal = { id: string; metric_type: string; target_cents: number };
type Unit = { key: string; label: string; units: number; net_gain_cents: number };
type Placement = { id: string; label: string; date: string; starts_at: string; ends_at: string; night: boolean };
type Scenario = {
  id: string; ranking: number; labels: string[]; units: Unit[]; placements: Placement[];
  net_gain_cents: number; net_gain_pessimistic_cents: number; active_minutes: number; committed_minutes: number;
  new_days_used: number; projected_after_cents: number; surplus_cents: number; pessimistic_ok: boolean;
  net_per_committed_hour_cents: number | null; binding_constraints: { code: string; label: string }[];
  risks: string[]; explanation: string[];
};
type Blocking = { labels: string[]; closes_gap: boolean; message: string; max_gain_if_relaxed_cents: number };
type Run = {
  run_id: string; outcome: string; feasible: boolean; baseline_cents: number; gap_cents: number; effective_target_cents: number;
  scenarios: Scenario[]; message?: string; max_achievable_gain_cents?: number; shortfall_cents?: number; max_projected_cents?: number;
  blocking_constraints?: Blocking[]; excluded_activities: { label: string; reason: string }[];
  conservative_feasible?: boolean | null; conservative_message?: string; assumptions: string[];
  info: { no_hours_limit: boolean; headroom: { active_minutes: number | null; committed_minutes: number | null } };
};
type Compare = { scenarios: { id: string; label: string; net_gain_cents: number; surplus_cents: number; active_minutes: number; committed_minutes: number; new_days_used: number; risks: number; pessimistic_ok: boolean; units: Record<string, number> }[] };
type Applied = { id: string; status: string; events_created?: number };

const thisMonth = () => new Date().toISOString().slice(0, 7);
const hoursToMin = (v: FormDataEntryValue | null) => (v && String(v).trim() ? Math.round(Number(String(v).replace(",", ".")) * 60) : null);

export default function OptimizerPage() {
  const { me, org } = useSession();
  const [month, setMonth] = useState(thisMonth());
  const [sources, setSources] = useState<Source[] | null>(null);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [compare, setCompare] = useState<Compare | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [applied, setApplied] = useState<Record<string, Applied>>({});
  const [createEvents, setCreateEvents] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  useEffect(() => {
    setRun(null); setCompare(null); setPicked([]);
    Promise.all([call<Source[]>(`/api/v1/income-plans?month=${month}`), call<Goal[]>(`/api/v1/goals?month=${month}`)])
      .then(([s, g]) => { setSources(s.filter((x) => x.eligible)); setGoals(g); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Falha ao carregar"));
  }, [call, month]);

  async function onRun(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null); setNotice(null); setCompare(null); setPicked([]);
    const f = new FormData(e.currentTarget);
    const activities = (sources ?? []).filter((s) => f.get(`use-${s.source_id}`) === "on").map((s) => {
      const base: Record<string, unknown> = { source_type: s.source_type, source_id: s.source_id, max_units: Number(f.get(`max-${s.source_id}`) || 0) };
      if (s.source_type === "WORK") {
        const days = String(f.get(`days-${s.source_id}`) || "").split(/[\s,;]+/).filter(Boolean);
        if (days.length) base.candidate_days = days.map((d) => `${month}-${String(Number(d)).padStart(2, "0")}`);
        if (f.get(`start-${s.source_id}`)) base.start_time = f.get(`start-${s.source_id}`);
      } else {
        const dem = String(f.get(`dem-${s.source_id}`) || "").trim();
        if (dem !== "") base.demand_limit = Number(dem);
        const acq = parseBRLToCents(String(f.get(`acq-${s.source_id}`) || "0"));
        base.acquisition_cost_cents = acq ?? 0;
      }
      return base;
    });
    if (activities.length === 0) return setError("Selecione ao menos uma atividade.");
    const goalId = String(f.get("goal") || "");
    const body: Record<string, unknown> = {
      month, activities,
      max_additional_active_minutes: hoursToMin(f.get("maxActive")),
      max_additional_committed_minutes: hoursToMin(f.get("maxCommitted")),
    };
    if (goalId) body.goal_id = goalId;
    else {
      const t = parseBRLToCents(String(f.get("target") || ""));
      if (t === null) return setError("Informe a meta (R$) ou escolha uma meta salva.");
      body.target_cents = t;
    }
    const budget = String(f.get("budget") || "").trim();
    if (budget) { const c = parseBRLToCents(budget); if (c === null) return setError("Orçamento de captação inválido."); body.marketing_budget_cents = c; }
    setBusy(true);
    try { setRun(await call<Run>("/api/v1/optimizer/runs", { method: "POST", body })); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Falha ao otimizar"); }
    finally { setBusy(false); }
  }

  async function act(fn: () => Promise<unknown>, ok: string) {
    setError(null); setNotice(null); setBusy(true);
    try { await fn(); setNotice(ok); } catch (e) { setError(e instanceof ApiError ? e.message : "Falha na operação"); } finally { setBusy(false); }
  }

  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < 5 ? [...p, id] : p));

  return (
    <>
      <h1>Otimizador Misto</h1>
      <p className="muted">Encontra combinações de plantões, sobreavisos e consultas para fechar a diferença da meta sem passar dos seus limites. Cálculo determinístico; nada é agendado nem registrado como realizado sem sua confirmação.</p>
      <label htmlFor="month">Competência</label>
      <input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} style={{ maxWidth: 220 }} />
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <form className="card" onSubmit={onRun} style={{ marginTop: 16 }}>
        <h2>Meta e limites</h2>
        <div className="grid2">
          <div>
            <label htmlFor="goal">Meta</label>
            <select id="goal" name="goal" defaultValue="">
              <option value="">Informar valor</option>
              {goals.map((g) => <option key={g.id} value={g.id}>{g.metric_type === "PERSONAL_NET" ? "Líquida pessoal" : g.metric_type === "CLINIC_PROFIT" ? "Lucro do consultório" : "Faturamento bruto"} · {formatBRL(g.target_cents)}</option>)}
            </select>
          </div>
          <div><label htmlFor="target">Ou meta em R$</label><input id="target" name="target" inputMode="decimal" placeholder="50.000,00" /></div>
          <div><label htmlFor="maxActive">Máx. horas ativas adicionais</label><input id="maxActive" name="maxActive" inputMode="decimal" placeholder="40" /></div>
          <div><label htmlFor="maxCommitted">Máx. horas comprometidas adicionais</label><input id="maxCommitted" name="maxCommitted" inputMode="decimal" placeholder="80" /></div>
          <div><label htmlFor="budget">Orçamento de captação (R$)</label><input id="budget" name="budget" inputMode="decimal" placeholder="opcional" /></div>
        </div>
        <p className="muted">Os limites do mês cadastrados em Agenda também valem. Deixe em branco para não limitar por aqui.</p>

        <h2>Atividades permitidas</h2>
        {sources === null ? <p aria-busy="true">Carregando…</p> : sources.length === 0 ? (
          <p className="muted">Cadastre trabalhos (Trabalhos &amp; Rendas) ou serviços (Consultório) primeiro.</p>
        ) : sources.map((s) => (
          <fieldset key={s.source_id} className="card" style={{ marginBottom: 12 }}>
            <legend><label style={{ fontWeight: 600, margin: 0 }}><input type="checkbox" name={`use-${s.source_id}`} /> {s.name} <span className="muted">({s.source_type === "WORK" ? "trabalho" : "serviço"})</span></label></legend>
            <div className="grid2">
              <div><label htmlFor={`max-${s.source_id}`}>{s.source_type === "WORK" ? "Oportunidades disponíveis no mês" : "Máx. pacientes novos"}</label><input id={`max-${s.source_id}`} name={`max-${s.source_id}`} type="number" min="0" defaultValue={s.source_type === "WORK" ? 3 : 10} /></div>
              {s.source_type === "WORK" ? (
                <>
                  <div><label htmlFor={`days-${s.source_id}`}>Dias possíveis (dia do mês)</label><input id={`days-${s.source_id}`} name={`days-${s.source_id}`} placeholder="ex.: 6, 13, 20 (vazio = qualquer dia livre)" /></div>
                  <div><label htmlFor={`start-${s.source_id}`}>Horário de início</label><input id={`start-${s.source_id}`} name={`start-${s.source_id}`} type="time" defaultValue="07:00" /></div>
                </>
              ) : (
                <>
                  <div><label htmlFor={`dem-${s.source_id}`}>Demanda estimada de pacientes</label><input id={`dem-${s.source_id}`} name={`dem-${s.source_id}`} type="number" min="0" placeholder="vazio = não informada" /></div>
                  <div><label htmlFor={`acq-${s.source_id}`}>Custo de captação por paciente (R$)</label><input id={`acq-${s.source_id}`} name={`acq-${s.source_id}`} inputMode="decimal" placeholder="0,00" /></div>
                </>
              )}
            </div>
          </fieldset>
        ))}
        <p><button type="submit" disabled={busy}>{busy ? "Otimizando…" : "Otimizar"}</button></p>
      </form>

      {run && (
        <section aria-label="Resultado" style={{ marginTop: 16 }}>
          <div className="card">
            <h2>Resultado</h2>
            <p>Base projetada: <strong>{formatBRL(run.baseline_cents)}</strong> · Meta: <strong>{formatBRL(run.effective_target_cents)}</strong> · Falta: <strong>{formatBRL(run.gap_cents)}</strong></p>
            {run.outcome === "ALREADY_MET" && <p>{run.message}</p>}
            {run.excluded_activities.map((x) => <p key={x.label} className="muted">Fora da conta — {x.label}: {x.reason}</p>)}
            {run.info.no_hours_limit && run.outcome === "FEASIBLE" && <p className="muted">Nenhum limite de horas informado: os cenários não têm teto de esforço.</p>}
          </div>

          {run.outcome === "INFEASIBLE" && (
            <div className="card" style={{ marginTop: 16 }} role="alert">
              <h2>Meta fora do alcance com estas restrições</h2>
              <p><strong>{run.message}</strong></p>
              <p>Ganho máximo possível: {formatBRL(run.max_achievable_gain_cents ?? 0)} · faltariam {formatBRL(run.shortfall_cents ?? 0)}.</p>
              <h3>O que está impedindo</h3>
              <ul>
                {run.blocking_constraints?.map((b, i) => (
                  <li key={i}><strong>{b.labels.join(" + ")}</strong>: {b.message} {b.closes_gap ? "Mudar isso fecharia a meta." : `Mudar isso levaria o máximo a ${formatBRL(run.baseline_cents + b.max_gain_if_relaxed_cents)}.`}</li>
                ))}
                {!run.blocking_constraints?.length && <li>Nenhuma restrição isolada explica; combine mais oportunidades, demanda e horas.</li>}
              </ul>
            </div>
          )}

          {run.conservative_feasible === false && <p className="muted">{run.conservative_message}</p>}

          {run.scenarios.map((s) => (
            <article key={s.id} className="card" style={{ marginTop: 16 }} aria-label={`Cenário ${s.ranking}`}>
              <h2>{s.ranking}. {s.labels.join(" · ")}</h2>
              <div className="kpis">
                <div className="kpi"><div className="label">Ganho líquido adicional</div><div className="value">{formatBRL(s.net_gain_cents)}</div></div>
                <div className="kpi"><div className="label">Resultado projetado</div><div className="value">{formatBRL(s.projected_after_cents)}</div><div className="muted">folga {formatBRL(s.surplus_cents)}</div></div>
                <div className="kpi"><div className="label">Horas ativas / comprometidas</div><div className="value">{formatMinutes(s.active_minutes)} / {formatMinutes(s.committed_minutes)}</div></div>
                <div className="kpi"><div className="label">Dias novos na agenda</div><div className="value">{s.new_days_used}</div></div>
              </div>
              <ul>{s.units.map((u) => <li key={u.key}><strong>{u.units}×</strong> {u.label} — {formatBRL(u.net_gain_cents)}</li>)}</ul>
              {s.placements.length > 0 && <p className="muted">Datas: {s.placements.map((p) => `${p.date.slice(8)}/${p.date.slice(5, 7)} ${p.starts_at.slice(11, 16)}${p.night ? " (noturno)" : ""}`).join(" · ")}</p>}
              <p>{s.pessimistic_ok ? "Fecha a meta mesmo no cenário pessimista." : <strong>Atenção: não fecha a meta se ganhos e demanda forem menores.</strong>} <span className="muted">(pessimista: {formatBRL(s.net_gain_pessimistic_cents)})</span></p>
              {s.risks.length > 0 && <ul>{s.risks.map((r) => <li key={r} className="muted">Risco: {r}</li>)}</ul>}
              <details><summary>Por que esse cenário?</summary>
                <ul>{s.explanation.map((x) => <li key={x}>{x}</li>)}</ul>
                {s.binding_constraints.length > 0 && <p className="muted">No limite: {s.binding_constraints.map((b) => b.label).join("; ")}.</p>}
              </details>
              <p>
                <label style={{ fontWeight: 400, display: "inline" }}><input type="checkbox" checked={picked.includes(s.id)} onChange={() => toggle(s.id)} /> Comparar</label>{" "}
                <button type="button" className="secondary" disabled={busy} onClick={() => act(() => call(`/api/v1/optimizer/scenarios/${s.id}/save`, { method: "POST" }), "Cenário salvo.")}>Salvar cenário</button>{" "}
                {applied[s.id] && applied[s.id].status === "APPLIED" ? (
                  <button type="button" className="secondary" disabled={busy} onClick={() => act(async () => { await call(`/api/v1/optimizer/saved-plans/${applied[s.id].id}/undo`, { method: "POST" }); setApplied((a) => ({ ...a, [s.id]: { ...a[s.id], status: "UNDONE" } })); }, "Aplicação desfeita.")}>Desfazer</button>
                ) : (
                  <button type="button" disabled={busy} onClick={() => act(async () => { const r = await call<Applied>(`/api/v1/optimizer/scenarios/${s.id}/apply`, { method: "POST", body: { create_events: createEvents } }); setApplied((a) => ({ ...a, [s.id]: r })); }, "Aplicado ao planejamento: veja a composição em Minha Meta. Nada foi marcado como realizado.")}>Aplicar ao planejamento</button>
                )}
              </p>
            </article>
          ))}

          {run.scenarios.length > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <label style={{ fontWeight: 400 }}><input type="checkbox" checked={createEvents} onChange={(e) => setCreateEvents(e.target.checked)} /> Ao aplicar, também colocar os plantões/sobreavisos como eventos <em>planejados</em> na agenda</label>
              <p><button type="button" className="secondary" disabled={picked.length < 2 || busy} onClick={() => act(async () => setCompare(await call<Compare>("/api/v1/optimizer/compare", { method: "POST", body: { scenario_ids: picked } })), "Comparação pronta.")}>Comparar cenários ({picked.length})</button></p>
              {compare && (
                <div className="tablewrap"><table>
                  <thead><tr><th>Cenário</th><th>Ganho</th><th>Folga</th><th>Ativas</th><th>Comprometidas</th><th>Dias novos</th><th>Riscos</th><th>Pessimista</th></tr></thead>
                  <tbody>{compare.scenarios.map((c) => <tr key={c.id}><td>{c.label}</td><td>{formatBRL(c.net_gain_cents)}</td><td>{formatBRL(c.surplus_cents)}</td><td>{formatMinutes(c.active_minutes)}</td><td>{formatMinutes(c.committed_minutes)}</td><td>{c.new_days_used}</td><td>{c.risks}</td><td>{c.pessimistic_ok ? "Fecha" : "Não fecha"}</td></tr>)}</tbody>
                </table></div>
              )}
            </div>
          )}
          <details className="card" style={{ marginTop: 16 }}><summary><strong>Premissas</strong></summary><ul>{run.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>
        </section>
      )}
    </>
  );
}
