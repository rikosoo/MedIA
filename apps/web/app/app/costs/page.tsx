"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents } from "@/lib/money";
import { formatMinutes } from "@/lib/time";
import { useSession } from "../session";

type Cat = { category: string; label: string; included: boolean; realized_cents: number; planned_cents: number; projected_cents: number; amortized_cents: number; total_cents: number };
type Svc = {
  service_id: string; name: string; planned_units: number; margin_per_consult_cents: number; margin_per_hour_cents: number | null;
  fixed_per_consult_cents: number | null; net_per_consult_cents: number | null; net_per_hour_cents: number | null;
  breakeven_alone_units: number | null; breakeven_units: number | null;
};
type Report = {
  month: string;
  fixed: { total_cents: number; computed_cents: number; manual: boolean; by_category: Cat[] };
  hours: { planned_minutes: number; available_minutes: number | null; available_source: string | null; basis: string; fixed_per_hour_planned_cents: number | null; fixed_per_hour_available_cents: number | null; idle_cost_cents: number | null };
  services: Svc[];
  breakeven: { status: string; planned_units: number; contribution_cents: number; result_cents: number; avg_margin_cents: number | null; total_units: number; minutes: number; safety_bps: number | null; occupancy_bps: number | null; fits_schedule: boolean | null };
  warnings: string[]; assumptions: string[];
};

const thisMonth = () => new Date().toISOString().slice(0, 7);
const money = (c: number | null) => (c === null ? "—" : formatBRL(c));
const pct = (bps: number | null) => (bps === null ? "—" : `${(bps / 100).toFixed(0).replace("-", "−")}%`);
const STATUS: Record<string, string> = {
  ABOVE: "Acima do equilíbrio", BELOW: "Abaixo do equilíbrio", NO_PLAN: "Sem consultas planejadas", UNREACHABLE: "Equilíbrio inalcançável (margem ≤ 0)",
};

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="muted">{hint}</div>}
    </div>
  );
}

export default function CostsPage() {
  const { org } = useSession();
  const [month, setMonth] = useState(thisMonth());
  const [picked, setPicked] = useState<string[] | null>(null); // null = padrão do servidor
  const [manualFixed, setManualFixed] = useState("");
  const [manualHours, setManualHours] = useState("");
  const [r, setR] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    const q = new URLSearchParams({ month });
    if (picked) q.set("categories", picked.join(","));
    const f = manualFixed.trim() ? parseBRLToCents(manualFixed) : null;
    if (manualFixed.trim() && f === null) return setError("Custo fixo manual inválido. Use o formato 6.000,00");
    if (f !== null) q.set("fixed_cents", String(f));
    const h = manualHours.trim() ? Number(manualHours.replace(",", ".")) : null;
    if (manualHours.trim() && (h === null || !Number.isFinite(h) || h < 0)) return setError("Horas inválidas.");
    if (h !== null) q.set("available_minutes", String(Math.round(h * 60)));
    api<Report>(`/api/v1/costs/breakeven?${q}`, { orgId: org.id }).then(setR).catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? "Você não tem permissão para ver custos e finanças." : e.message) : "Falha ao carregar"));
  }, [month, picked, manualFixed, manualHours, org.id]);
  useEffect(load, [load]);

  const toggle = (code: string) => {
    const current = picked ?? r?.fixed.by_category.filter((c) => c.included).map((c) => c.category) ?? [];
    setPicked(current.includes(code) ? current.filter((c) => c !== code) : [...current, code]);
  };
  const be = r?.breakeven;

  return (
    <div>
      <h1>Custos &amp; Equilíbrio</h1>
      <p className="muted">Quanto custa manter o consultório, quanto isso pesa em cada hora e quantas consultas pagam os custos fixos. <strong>É uma estimativa antes de tributos</strong>, calculada com o que você cadastrou (não é dinheiro recebido).</p>
      <label htmlFor="month">Competência</label>
      <input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} style={{ maxWidth: 220 }} />
      {error && <p role="alert" className="error">{error}</p>}
      {!r && !error && <p aria-busy="true">Carregando…</p>}

      {r && be && (
        <>
          {r.warnings.map((w) => <p key={w} className="muted">• {w}</p>)}
          <div className="kpis">
            <Kpi label="Custo fixo do mês" value={formatBRL(r.fixed.total_cents)} hint={r.fixed.manual ? "valor informado por você" : "soma das categorias marcadas"} />
            <Kpi label="Custo fixo por hora" value={money(r.hours.basis === "PLANNED" ? r.hours.fixed_per_hour_planned_cents : r.hours.fixed_per_hour_available_cents)} hint={r.hours.basis === "PLANNED" ? "sobre as horas planejadas" : "sobre as horas disponíveis"} />
            <Kpi label="Resultado após custos fixos" value={formatBRL(be.result_cents)} hint={STATUS[be.status]} />
            <Kpi label="Ponto de equilíbrio" value={be.total_units ? `${be.total_units} consultas` : "—"} hint={be.total_units ? `${formatMinutes(be.minutes)} de atendimento${be.occupancy_bps !== null ? ` · ${pct(be.occupancy_bps)} da agenda` : ""}` : undefined} />
          </div>

          {be.status === "BELOW" && <p role="status" className="card">Com o que está planejado ({be.planned_units} consultas), a contribuição é {formatBRL(be.contribution_cents)} e <strong>não cobre os custos fixos</strong>. O equilíbrio exige {be.total_units} consultas{be.fits_schedule === false ? ", e isso não cabe nas horas disponíveis" : ""}.</p>}
          {be.status === "ABOVE" && be.safety_bps !== null && <p role="status" className="card">O planejado cobre os custos fixos com folga de {pct(be.safety_bps)} (pode cair {pct(be.safety_bps)} sem ficar no prejuízo, mantidas as demais condições).</p>}
          {r.hours.idle_cost_cents !== null && r.hours.idle_cost_cents > 0 && <p className="muted">Horas disponíveis sem consulta planejada custam cerca de {formatBRL(r.hours.idle_cost_cents)} do custo fixo (capacidade ociosa).</p>}

          <section className="card" aria-labelledby="c-fixed">
            <h2 id="c-fixed">O que conta como custo fixo</h2>
            <p className="muted">Marque ou desmarque as categorias. Aluguel, secretária e equipamentos entram por padrão; anuais são divididos por 12.</p>
            <div className="tablewrap">
              <table>
                <thead><tr><th>Incluir</th><th>Categoria</th><th>Realizado</th><th>Planejado</th><th>Recorrente</th><th>Total no mês</th></tr></thead>
                <tbody>
                  {r.fixed.by_category.map((c) => (
                    <tr key={c.category}>
                      <td><input type="checkbox" checked={c.included} onChange={() => toggle(c.category)} aria-label={`Contar ${c.label} como custo fixo`} /></td>
                      <td>{c.label}</td><td>{formatBRL(c.realized_cents)}</td><td>{formatBRL(c.planned_cents)}</td><td>{formatBRL(c.projected_cents + c.amortized_cents)}</td><td>{formatBRL(c.total_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {r.fixed.by_category.every((c) => c.total_cents === 0) && <p>Nenhum custo cadastrado ainda. Cadastre aluguel da sala, secretária e equipamentos alugados como <Link href="/app/finances">despesas recorrentes</Link>.</p>}
            <p className="muted">Quer testar outro número? Os campos abaixo substituem o cálculo.</p>
            <div className="grid2">
              <div><label htmlFor="mf">Custo fixo do mês (R$)</label><input id="mf" inputMode="decimal" placeholder={formatBRL(r.fixed.computed_cents).replace("R$ ", "")} value={manualFixed} onChange={(e) => setManualFixed(e.target.value)} /></div>
              <div><label htmlFor="mh">Horas disponíveis no mês (clínica toda)</label><input id="mh" inputMode="decimal" placeholder={r.hours.available_minutes ? String(Math.round(r.hours.available_minutes / 60)) : "ex.: 160"} value={manualHours} onChange={(e) => setManualHours(e.target.value)} /></div>
            </div>
            {r.hours.available_source === "AGENDA" && <p className="muted">Horas disponíveis vindas da sua agenda: {formatMinutes(r.hours.available_minutes ?? 0)}. Em clínica com vários profissionais, informe as horas totais.</p>}
          </section>

          <section className="card" aria-labelledby="c-svc">
            <h2 id="c-svc">Margem real por serviço</h2>
            {r.services.length === 0 ? <p>Cadastre um <Link href="/app/clinic">serviço do consultório</Link> para ver a margem.</p> : (
              <div className="tablewrap">
                <table>
                  <thead><tr><th>Serviço</th><th>Planejadas</th><th>Margem/consulta</th><th>Custo fixo/consulta</th><th>Líquido/consulta</th><th>Líquido/hora</th><th>Equilíbrio (só ele)</th></tr></thead>
                  <tbody>
                    {r.services.map((s) => (
                      <tr key={s.service_id}>
                        <td>{s.name}</td><td>{s.planned_units}</td><td>{formatBRL(s.margin_per_consult_cents)}</td><td>{money(s.fixed_per_consult_cents)}</td>
                        <td>{money(s.net_per_consult_cents)}</td><td>{money(s.net_per_hour_cents)}</td><td>{s.breakeven_alone_units ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <details>
            <summary>Como calculamos</summary>
            <ul>{r.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
          </details>
        </>
      )}
    </div>
  );
}
