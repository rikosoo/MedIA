"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Onboarding } from "@/components/Onboarding";
import { MonthBars, type SeriesPoint } from "@/components/MonthBars";
import { RealizedPanel } from "@/components/RealizedPanel";
import { api, ApiError } from "@/lib/api";
import { formatBRL } from "@/lib/money";
import { formatMinutes } from "@/lib/time";
import { useSession } from "../session";

type Row = {
  name: string; kind: string; certainty: string; units: number; gross_cents: number; tax_cents: number;
  variable_cost_cents: number; net_cents: number; committed_minutes: number; net_per_committed_hour_cents: number | null;
};
type Dash = {
  month: string; metric_type: string; data_quality: string; data_quality_reasons: string[];
  goal: null | { goal: { target_cents: number }; effective_target_cents: number; projected_cents: number; assured_cents: number;
    realized_cents: number; gap_cents: number; assured_gap_cents: number; status: string };
  projection: { gross_cents: number; income_net_cents: number; expenses_cents: number; value_cents: number; assured_cents: number; probable_cents: number };
  realized: { income_cents: number; expense_cents: number; value_cents: number };
  expenses_detail: { realized_cents: number; planned_cents: number };
  breakdown: Row[]; series: SeriesPoint[];
  hours: null | { active_minutes: number; committed_minutes: number; planned_committed_minutes: number; schedule_feasible: boolean };
  alerts: { severity: string; code: string; message: string }[]; assumptions: string[];
  next_actions: { label: string; route: string }[];
};

const METRICS: Record<string, string> = { PERSONAL_NET: "Ganho líquido pessoal", CLINIC_PROFIT: "Lucro do consultório", GROSS_REVENUE: "Faturamento bruto" };
const QUALITY: Record<string, string> = { complete: "Dados completos", partial: "Dados parciais", insufficient: "Dados insuficientes" };
const thisMonth = () => new Date().toISOString().slice(0, 7);

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="muted">{hint}</div>}
    </div>
  );
}

export default function Dashboard() {
  const { me, org } = useSession();
  const [month, setMonth] = useState(thisMonth());
  const [d, setD] = useState<Dash | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api<Dash>(`/api/v1/dashboard?month=${month}`, { orgId: org.id })
      .then(setD)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Falha ao carregar o painel"));
  }, [month, org.id]);
  useEffect(load, [load]);

  const g = d?.goal;
  const diff = g ? (g.status === "SHORT" ? `Faltam ${formatBRL(g.gap_cents)}` : `Meta atingida · folga de ${formatBRL(g.projected_cents - g.effective_target_cents)}`) : "Sem meta definida";

  return (
    <>
      <h1>Olá, {me.name}</h1>
      <label htmlFor="month">Competência</label>
      <input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} style={{ maxWidth: 220 }} />
      <Onboarding orgId={org.id} />
      {error && <p className="error" role="alert">{error}</p>}
      {!d && !error && <p aria-busy="true">Carregando…</p>}

      {d && (
        <>
          <p className="muted">
            {METRICS[d.metric_type]} · <strong>{QUALITY[d.data_quality]}</strong>
          </p>
          {d.data_quality_reasons.map((r) => <p key={r} className="muted">• {r}</p>)}

          <div className="kpis">
            <Kpi label="Meta (líquida desejada)" value={g ? formatBRL(g.effective_target_cents) : "—"} hint={g ? undefined : "Defina em Minha Meta"} />
            <Kpi label="Projetado" value={formatBRL(d.projection.value_cents)} hint={`${formatBRL(d.projection.assured_cents)} garantido`} />
            <Kpi label="Realizado até agora" value={formatBRL(d.realized.value_cents)} hint="lançamentos com data de caixa" />
            <Kpi label="Diferença para a meta" value={g ? (g.status === "SHORT" ? formatBRL(-g.gap_cents) : "Atingida") : "—"} hint={diff} />
          </div>

          {d.alerts.length > 0 && (
            <div className="card" style={{ marginTop: 16 }} role="region" aria-label="Alertas">
              <h2>Alertas</h2>
              <ul>{d.alerts.map((a) => <li key={a.code}><strong>{a.severity === "warning" ? "Atenção:" : "Info:"}</strong> {a.message}</li>)}</ul>
            </div>
          )}

          <div className="card" style={{ marginTop: 16 }}>
            <h2>Últimos 12 meses</h2>
            <MonthBars series={d.series} />
          </div>

          <div className="card tablewrap" style={{ marginTop: 16 }}>
            <h2>De onde vem o projetado</h2>
            {d.breakdown.length === 0 ? (
              <p className="muted">Nenhuma fonte planejada para {month}. <Link href="/app/goals">Monte a composição do mês</Link>.</p>
            ) : (
              <table>
                <thead><tr><th>Fonte</th><th>Certeza</th><th>Unid.</th><th>Bruto</th><th>Retenção est.*</th><th>Líquido</th><th>Líq./h comprometida</th></tr></thead>
                <tbody>
                  {d.breakdown.map((r) => (
                    <tr key={r.name + r.kind}>
                      <td>{r.name}<br /><span className="muted">{r.kind === "WORK" ? "Trabalho" : "Serviço do consultório"}</span></td>
                      <td>{r.certainty === "GUARANTEED" ? "Garantido" : "Provável"}</td>
                      <td>{r.units}</td><td>{formatBRL(r.gross_cents)}</td><td>{formatBRL(r.tax_cents)}</td><td>{formatBRL(r.net_cents)}</td>
                      <td>{r.net_per_committed_hour_cents === null ? "—" : formatBRL(r.net_per_committed_hour_cents)}</td>
                    </tr>
                  ))}
                  <tr><td colSpan={5}><strong>Despesas do mês</strong> (realizadas {formatBRL(d.expenses_detail.realized_cents)} + planejadas {formatBRL(d.expenses_detail.planned_cents)})</td><td colSpan={2}>−{formatBRL(d.projection.expenses_cents)}</td></tr>
                  <tr><td colSpan={5}><strong>Resultado projetado</strong></td><td colSpan={2}><strong>{formatBRL(d.projection.value_cents)}</strong></td></tr>
                </tbody>
              </table>
            )}
            <p className="muted">* Estimativa informada por você; não é consultoria tributária.</p>
          </div>

          <RealizedPanel month={month} onChanged={load} />

          <div className="grid2" style={{ marginTop: 16 }}>
            <div className="card">
              <h2>Tempo</h2>
              {d.hours ? (
                <>
                  <p>Agenda: <strong>{formatMinutes(d.hours.active_minutes)}</strong> ativas · <strong>{formatMinutes(d.hours.committed_minutes)}</strong> comprometidas</p>
                  <p>Planejado nas fontes: {formatMinutes(d.hours.planned_committed_minutes)} comprometidas</p>
                  <p>{d.hours.schedule_feasible ? "Agenda dentro dos limites." : "Atenção: a agenda tem conflitos ou limites estourados."}</p>
                </>
              ) : <p className="muted">Sem acesso à agenda.</p>}
            </div>
            <div className="card">
              <h2>Próximos passos</h2>
              <ul>{d.next_actions.map((a) => <li key={a.route}><Link href={a.route}>{a.label}</Link></li>)}</ul>
              <Link href="/app/optimizer" className="btn">Otimizar minha meta</Link>
            </div>
          </div>

          <details className="card" style={{ marginTop: 16 }}>
            <summary><strong>Como calculamos</strong></summary>
            <ul>{d.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
          </details>
        </>
      )}
    </>
  );
}
