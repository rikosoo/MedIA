"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents, percentToBps } from "@/lib/money";
import { useSession } from "../session";

type Goal = { id: string; month: string; metric_type: string; target_cents: number; priority: string; use_reserves: boolean; reserve_cents: number };
type Plan = { id: string; units: number; certainty: string; tax_rate_bps: number | null; inherited: boolean };
type Source = { source_type: string; source_id: string; name: string; eligible: boolean; plan: Plan | null };
type Gap = {
  effective_target_cents: number; projected_cents: number; assured_cents: number; probable_cents: number; realized_cents: number;
  gap_cents: number; assured_gap_cents: number; realized_gap_cents: number; status: string;
};

const METRICS: Record<string, string> = { PERSONAL_NET: "Líquida pessoal", CLINIC_PROFIT: "Lucro do consultório", GROSS_REVENUE: "Faturamento bruto" };
const PRIORITIES: Record<string, string> = { BALANCED: "Equilíbrio", LESS_HOURS: "Menos horas", MORE_INCOME: "Mais renda" };
const STATUS: Record<string, string> = { SHORT: "Abaixo da meta", MET: "Meta atingida", MET_WITH_ISSUES: "Meta atingida, mas a agenda tem problemas" };
const thisMonth = () => new Date().toISOString().slice(0, 7);
const nextMonth = (m: string) => { const [y, mm] = m.split("-").map(Number); const d = new Date(y, mm, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; };

export default function GoalsPage() {
  const { me, org } = useSession();
  const [month, setMonth] = useState(thisMonth());
  const [metric, setMetric] = useState("PERSONAL_NET");
  const [goals, setGoals] = useState<Goal[]>([]);
  const [history, setHistory] = useState<Goal[]>([]);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [gap, setGap] = useState<Gap | null>(null);
  const [useReserves, setUseReserves] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const goal = goals.find((g) => g.metric_type === metric) ?? null;
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  const load = useCallback(async () => {
    try {
      const [gs, hist, src] = await Promise.all([
        call<Goal[]>(`/api/v1/goals?month=${month}`), call<Goal[]>("/api/v1/goals"), call<Source[]>(`/api/v1/income-plans?month=${month}`),
      ]);
      setGoals(gs); setHistory(hist); setSources(src);
      const g = gs.find((x) => x.metric_type === metric);
      setUseReserves(g?.use_reserves ?? false);
      setGap(g ? await call<Gap>(`/api/v1/goals/${g.id}/gap`) : null);
    } catch (e) { setError(e instanceof ApiError ? e.message : "Falha ao carregar"); }
  }, [call, month, metric]);
  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); if (ok) setNotice(ok); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Falha na operação"); } finally { setBusy(false); }
  }

  function onGoal(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const target = parseBRLToCents(String(f.get("target")));
    const reserve = parseBRLToCents(String(f.get("reserve") || "0"));
    if (target === null || reserve === null) return setError("Valor inválido. Use o formato 50.000,00");
    const body = { target_cents: target, priority: f.get("priority"), use_reserves: useReserves, reserve_cents: reserve };
    run(() => goal
      ? call(`/api/v1/goals/${goal.id}`, { method: "PATCH", body })
      : call("/api/v1/goals", { method: "POST", body: { ...body, month, metric_type: metric } }), "Meta salva.");
  }

  function onPlan(e: React.FormEvent<HTMLFormElement>, s: Source) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const taxRaw = String(f.get("tax") || "").trim();
    const tax = taxRaw === "" ? null : percentToBps(taxRaw);
    if (taxRaw !== "" && tax === null) return setError("Retenção inválida (0 a 100%).");
    run(() => call("/api/v1/income-plans", {
      method: "PUT",
      body: { month, source_type: s.source_type, source_id: s.source_id, units: Number(f.get("units")), certainty: f.get("certainty"), tax_rate_bps: tax },
    }), `Composição de "${s.name}" salva para ${month}.`);
  }

  return (
    <>
      <h1>Minha Meta</h1>
      <div className="grid2">
        <div><label htmlFor="month">Competência</label><input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} /></div>
        <div>
          <label htmlFor="metric">Tipo de meta</label>
          <select id="metric" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {Object.entries(METRICS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <form className="card" onSubmit={onGoal} key={goal?.id ?? `new-${month}-${metric}`} style={{ marginTop: 16 }}>
        <h2>{goal ? "Meta do mês" : "Definir meta"}</h2>
        <div className="grid2">
          <div><label htmlFor="target">Quanto quer receber (R$)</label><input id="target" name="target" inputMode="decimal" required placeholder="50.000,00" defaultValue={goal ? (goal.target_cents / 100).toFixed(2).replace(".", ",") : ""} /></div>
          <div>
            <label htmlFor="priority">Preferência de esforço</label>
            <select id="priority" name="priority" defaultValue={goal?.priority ?? "BALANCED"}>{Object.entries(PRIORITIES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          </div>
          <div>
            <label htmlFor="reserve">Reserva que poderia usar (R$)</label>
            <input id="reserve" name="reserve" inputMode="decimal" placeholder="0,00" defaultValue={goal && goal.reserve_cents ? (goal.reserve_cents / 100).toFixed(2).replace(".", ",") : ""} />
          </div>
          <div><label htmlFor="usereserve" style={{ fontWeight: 400 }}><input id="usereserve" type="checkbox" checked={useReserves} onChange={(e) => setUseReserves(e.target.checked)} style={{ width: "auto", minHeight: 0 }} /> Abater a reserva da meta (desligado por padrão)</label></div>
        </div>
        <p>
          <button type="submit" disabled={busy}>Salvar meta</button>{" "}
          {goal && <button type="button" className="secondary" disabled={busy} onClick={() => run(() => call(`/api/v1/goals/${goal.id}/duplicate`, { method: "POST", body: { month: nextMonth(month) } }), `Meta copiada para ${nextMonth(month)}.`)}>Duplicar para {nextMonth(month)}</button>}
        </p>
      </form>

      {gap && goal && (
        <div className="card" style={{ marginTop: 16 }} role="region" aria-label="Diferença para a meta">
          <h2>Diferença para a meta</h2>
          <p><strong>{STATUS[gap.status]}</strong></p>
          <p>Meta considerada: {formatBRL(gap.effective_target_cents)} · Projetado: <strong>{formatBRL(gap.projected_cents)}</strong></p>
          <p>{gap.gap_cents > 0 ? <>Faltam <strong>{formatBRL(gap.gap_cents)}</strong>.</> : <>Sem lacuna na projeção.</>}</p>
          <p className="muted">Garantido: {formatBRL(gap.assured_cents)} (faltariam {formatBRL(gap.assured_gap_cents)}) · Provável: {formatBRL(gap.probable_cents)} · Realizado até agora: {formatBRL(gap.realized_cents)} (faltam {formatBRL(gap.realized_gap_cents)})</p>
          <p className="muted">Veja a decomposição completa no Início. Tributos são estimativas não validadas.</p>
        </div>
      )}

      <h2>Composição do mês ({month})</h2>
      <p className="muted">Quantas unidades de cada fonte você espera. Deixe a retenção em branco para usar a do cadastro. O consultório entra por serviços, nunca como trabalho, para a renda não ser contada duas vezes.</p>
      {sources === null ? <p aria-busy="true">Carregando…</p> : sources.length === 0 ? (
        <p className="muted">Cadastre trabalhos em Trabalhos &amp; Rendas ou serviços em Consultório.</p>
      ) : sources.map((s) => (
        <form key={s.source_type + s.source_id + month + (s.plan?.units ?? "x")} className="card" style={{ marginBottom: 12 }} onSubmit={(e) => onPlan(e, s)}>
          <strong>{s.name}</strong> <span className="muted">{s.source_type === "WORK" ? "Trabalho" : "Serviço"}</span>
          {!s.eligible ? <p className="muted">Consultório cadastrado como trabalho: planeje pelos serviços abaixo.</p> : (
            <>
              <div className="grid2">
                <div><label htmlFor={`u-${s.source_id}`}>{s.source_type === "SERVICE" ? "Pacientes novos esperados" : "Unidades no mês"}</label><input id={`u-${s.source_id}`} name="units" type="number" min="0" required defaultValue={s.plan?.units ?? 0} /></div>
                <div>
                  <label htmlFor={`c-${s.source_id}`}>Certeza</label>
                  <select id={`c-${s.source_id}`} name="certainty" defaultValue={s.plan?.certainty ?? "PROBABLE"}><option value="GUARANTEED">Garantido</option><option value="PROBABLE">Provável</option></select>
                </div>
                <div><label htmlFor={`t-${s.source_id}`}>Retenção estimada (%)</label><input id={`t-${s.source_id}`} name="tax" inputMode="decimal" defaultValue={s.plan?.tax_rate_bps != null ? String(s.plan.tax_rate_bps / 100).replace(".", ",") : ""} /></div>
              </div>
              {s.plan?.inherited && <p className="muted">Herdando o padrão mensal ({s.plan.units} unid.). Salvar cria o plano deste mês.</p>}
              <p><button type="submit" disabled={busy}>Salvar composição</button></p>
            </>
          )}
        </form>
      ))}

      <h2>Histórico de metas</h2>
      {history.length === 0 ? <p className="muted">Nenhuma meta ainda.</p> : (
        <div className="tablewrap card"><table>
          <thead><tr><th>Mês</th><th>Tipo</th><th>Meta</th><th>Preferência</th></tr></thead>
          <tbody>{history.map((h) => <tr key={h.id}><td>{h.month}</td><td>{METRICS[h.metric_type]}</td><td>{formatBRL(h.target_cents)}{h.use_reserves ? ` (− reserva ${formatBRL(h.reserve_cents)})` : ""}</td><td>{PRIORITIES[h.priority]}</td></tr>)}</tbody>
        </table></div>
      )}
    </>
  );
}
