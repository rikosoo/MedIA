"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents } from "@/lib/money";
import { useSession } from "../session";

type Channel = { id: string; name: string; channel_type: string; active: boolean };
type F = {
  leads: number; responded_in_time: number; bookings: number; attended: number; spend_cents: number;
  response_rate_bps: number | null; lead_to_booking_bps: number | null; booking_to_attendance_bps: number | null;
  cost_per_booking_cents: number | null; cost_per_attended_cents: number | null;
};
type Row = F & { channel: Channel; has_data: boolean };
type FunnelView = {
  total: F; by_channel: (F & { channel: Channel; attributed_revenue_cents?: number; roi_bps?: number | null })[];
  bottleneck: { label: string; recommendations: string[]; note: string } | null; messages: string[];
  attribution: { basis: string; revenue_per_attended_cents: number; attributed_revenue_cents: number; spend_cents: number; roi_bps: number | null; note: string } | null;
  secretary_coverage_bps: number; has_data: boolean;
};
type Plan = { enough_data?: boolean; message?: string; leads_needed?: number | null; bookings_needed?: number | null; messages?: string[]; capacity?: { new_patients_remaining: number } | null };

const TYPES: Record<string, string> = { GOOGLE: "Google", REFERRAL: "Indicação", INSTAGRAM: "Instagram", SITE: "Site", SPONTANEOUS_RETURN: "Retorno espontâneo", OTHER: "Outro" };
const thisMonth = () => new Date().toISOString().slice(0, 7);
const pct = (b: number | null) => (b === null ? "—" : `${Math.round(b / 100)}%`);
const money = (c: number | null) => (c === null ? "—" : formatBRL(c));

export default function AcquisitionPage() {
  const { me, org } = useSession();
  const [month, setMonth] = useState(thisMonth());
  const [channels, setChannels] = useState<Channel[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [view, setView] = useState<FunnelView | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  const load = useCallback(async () => {
    try {
      const [c, r, v] = await Promise.all([
        call<Channel[]>("/api/v1/acquisition/channels"), call<Row[]>(`/api/v1/acquisition/metrics?month=${month}`), call<FunnelView>(`/api/v1/acquisition/funnel?month=${month}`),
      ]);
      setChannels(c); setRows(r); setView(v);
    } catch (e) { setError(e instanceof ApiError ? e.message : "Falha ao carregar"); }
  }, [call, month]);
  useEffect(() => { setPlan(null); load(); }, [load]);

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setError(null); setNotice(null);
    try { await fn(); if (ok) setNotice(ok); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Falha na operação"); }
  }

  function onChannel(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const form = e.currentTarget;
    run(async () => { await call("/api/v1/acquisition/channels", { method: "POST", body: { name: f.get("name"), channel_type: f.get("type") } }); form.reset(); }, "Canal criado.");
  }

  function onMetric(e: React.FormEvent<HTMLFormElement>, ch: Channel) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const spend = parseBRLToCents(String(f.get("spend") || "0"));
    if (spend === null) return setError("Gasto inválido. Use o formato 600,00");
    run(() => call("/api/v1/acquisition/metrics", { method: "PUT", body: {
      month, channel_id: ch.id, leads_count: Number(f.get("leads")), responded_in_time_count: Number(f.get("resp")),
      bookings_count: Number(f.get("book")), attended_count: Number(f.get("att")), spend_cents: spend } }), `Métricas de ${ch.name} salvas.`);
  }

  function onPlan(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const n = Number(new FormData(e.currentTarget).get("target"));
    setError(null);
    call<Plan>(`/api/v1/acquisition/plan?month=${month}&target_new_patients=${n}`).then(setPlan).catch((err) => setError(err instanceof ApiError ? err.message : "Falha no cálculo"));
  }

  return (
    <>
      <h1>Captação</h1>
      <p className="muted">Só números agregados por canal: nenhum dado de paciente. Os resultados dependem do que você registra; contatos comprados ou mensagens sem autorização não fazem parte do app.</p>
      <label htmlFor="month">Mês</label>
      <input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} style={{ maxWidth: 220 }} />
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <form className="card" onSubmit={onChannel} style={{ marginTop: 16 }}>
        <h2>Canais</h2>
        <div className="grid2">
          <div><label htmlFor="cname">Nome do canal</label><input id="cname" name="name" required maxLength={100} placeholder="Google Meu Negócio" /></div>
          <div><label htmlFor="ctype">Tipo</label><select id="ctype" name="type">{Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        </div>
        <p><button type="submit">Adicionar canal</button></p>
        {channels.length > 0 && <ul>{channels.map((c) => <li key={c.id}>{c.name} <span className="muted">({TYPES[c.channel_type]})</span>{" "}<button type="button" className="secondary" onClick={() => run(() => call(`/api/v1/acquisition/channels/${c.id}`, { method: "PATCH", body: { active: false } }), "Canal desativado; o histórico continua.")} aria-label={`Desativar ${c.name}`}>Desativar</button></li>)}</ul>}
      </form>

      {rows.map((r) => (
        <form key={r.channel.id + month + String(r.has_data) + r.leads} className="card" style={{ marginTop: 12 }} onSubmit={(e) => onMetric(e, r.channel)} aria-label={`Métricas de ${r.channel.name}`}>
          <h3>{r.channel.name} — {month}</h3>
          <div className="grid2">
            <div><label htmlFor={`l-${r.channel.id}`}>Contatos (leads)</label><input id={`l-${r.channel.id}`} name="leads" type="number" min="0" required defaultValue={r.leads} /></div>
            <div><label htmlFor={`r-${r.channel.id}`}>Respondidos no prazo</label><input id={`r-${r.channel.id}`} name="resp" type="number" min="0" required defaultValue={r.responded_in_time} /></div>
            <div><label htmlFor={`b-${r.channel.id}`}>Agendamentos</label><input id={`b-${r.channel.id}`} name="book" type="number" min="0" required defaultValue={r.bookings} /></div>
            <div><label htmlFor={`a-${r.channel.id}`}>Comparecimentos</label><input id={`a-${r.channel.id}`} name="att" type="number" min="0" required defaultValue={r.attended} /></div>
            <div><label htmlFor={`s-${r.channel.id}`}>Gasto no mês (R$)</label><input id={`s-${r.channel.id}`} name="spend" inputMode="decimal" defaultValue={r.spend_cents ? (r.spend_cents / 100).toFixed(2).replace(".", ",") : ""} placeholder="0,00" /></div>
          </div>
          <p><button type="submit">Salvar métricas</button> <span className="muted">Agendamentos ≤ contatos; comparecimentos ≤ agendamentos.</span></p>
        </form>
      ))}

      {view && view.has_data && (
        <div className="card" style={{ marginTop: 16 }} role="region" aria-label="Funil do mês">
          <h2>Funil de {month}</h2>
          <div className="kpis">
            <div className="kpi"><div className="label">Contatos → agendamentos</div><div className="value">{view.total.leads} → {view.total.bookings}</div><div className="muted">{pct(view.total.lead_to_booking_bps)}</div></div>
            <div className="kpi"><div className="label">Agend. → comparecimentos</div><div className="value">{view.total.bookings} → {view.total.attended}</div><div className="muted">{pct(view.total.booking_to_attendance_bps)}</div></div>
            <div className="kpi"><div className="label">Custo por agendamento</div><div className="value">{money(view.total.cost_per_booking_cents)}</div></div>
            <div className="kpi"><div className="label">Custo por comparecimento</div><div className="value">{money(view.total.cost_per_attended_cents)}</div></div>
          </div>
          {view.messages.map((m) => <p key={m} className="muted">• {m}</p>)}
          <p className="muted">Cobertura da secretaria no mês: {pct(view.secretary_coverage_bps)}.</p>
          {view.bottleneck && (
            <div>
              <h3>Gargalo: {view.bottleneck.label}</h3>
              <ul>{view.bottleneck.recommendations.map((r) => <li key={r}>{r}</li>)}</ul>
              <p className="muted">{view.bottleneck.note}</p>
            </div>
          )}
          <div className="tablewrap"><table>
            <thead><tr><th>Canal</th><th>Contatos</th><th>Agend.</th><th>Compar.</th><th>Contato → agend.</th><th>Gasto</th><th>Custo/agend.</th>{view.attribution && <th>Receita atribuída</th>}</tr></thead>
            <tbody>{view.by_channel.map((c) => <tr key={c.channel.id}><td>{c.channel.name}</td><td>{c.leads}</td><td>{c.bookings}</td><td>{c.attended}</td><td>{pct(c.lead_to_booking_bps)}</td><td>{money(c.spend_cents)}</td><td>{money(c.cost_per_booking_cents)}</td>{view.attribution && <td>{money(c.attributed_revenue_cents ?? null)}{c.roi_bps != null && <><br /><span className="muted">retorno {pct(c.roi_bps)}</span></>}</td>}</tr>)}</tbody>
          </table></div>
          {view.attribution && <p className="muted">{view.attribution.note} Base: {view.attribution.basis} ({formatBRL(view.attribution.revenue_per_attended_cents)} por paciente).</p>}
        </div>
      )}

      <form className="card" style={{ marginTop: 16 }} onSubmit={onPlan}>
        <h2>Quantos contatos preciso?</h2>
        <label htmlFor="target">Pacientes novos desejados no mês</label>
        <input id="target" name="target" type="number" min="0" required defaultValue={10} style={{ maxWidth: 220 }} />
        <p><button type="submit">Calcular</button></p>
        {plan && (
          <div role="status">
            {plan.message && <p>{plan.message}</p>}
            {plan.leads_needed != null && <p>Com as suas taxas recentes: cerca de <strong>{plan.leads_needed}</strong> contatos e <strong>{plan.bookings_needed}</strong> agendamentos.</p>}
            {plan.messages?.map((m) => <p key={m} className="error">{m}</p>)}
            {plan.capacity && <p className="muted">A agenda comporta {plan.capacity.new_patients_remaining} paciente(s) novo(s) no mês.</p>}
          </div>
        )}
      </form>
    </>
  );
}
