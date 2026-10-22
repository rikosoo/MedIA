"use client";

import { useCallback, useEffect, useState } from "react";
import { ForecastChart, type FMonth } from "@/components/ForecastChart";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents, percentToBps } from "@/lib/money";
import { useSession } from "../session";

type Month = FMonth & {
  working_days: number; working_days_lost: number; plan_income_cents: number; expenses_cents: number;
  income_cents: { conservative: number; base: number; optimistic: number }; drivers: string[]; flags: string[];
};
type Result = {
  from_month: string; horizon_months: number; months: Month[]; data_quality: string; saved_id?: string;
  totals: { conservative: number; base: number; optimistic: number };
  reserve: Record<string, { reserve_cents: number; months_below_floor: number }> | null;
  assumptions: { attainment: { source: string; months_used: number; conservative_bps: number; base_bps: number; optimistic_bps: number }; seasonality: { requested: boolean; applied: boolean }; notes: string[] };
};
type Saved = { id: string; name: string | null; from_month: string; to_month: string; created_at: string };
type Adj = { month: string; label: string; income_delta_cents: number; expense_cents: number };

export default function ForecastsPage() {
  const { me, org } = useSession();
  const [res, setRes] = useState<Result | null>(null);
  const [saved, setSaved] = useState<Saved[]>([]);
  const [adjs, setAdjs] = useState<Adj[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);
  const loadSaved = useCallback(() => { call<Saved[]>("/api/v1/forecasts").then(setSaved).catch(() => setSaved([])); }, [call]);
  useEffect(loadSaved, [loadSaved]);

  async function runWith(form: HTMLFormElement, save: boolean) {
    const f = new FormData(form);
    setError(null); setNotice(null);
    const floor = String(f.get("floor") || "").trim();
    const floorC = floor ? parseBRLToCents(floor) : null;
    if (floor && floorC === null) return setError("Piso inválido. Use o formato 8.000,00");
    const infl = String(f.get("infl") || "0").trim();
    const inflBps = infl === "0" || infl === "" ? 0 : (infl.startsWith("-") ? -1 : 1) * (percentToBps(infl.replace("-", "")) ?? NaN);
    if (!Number.isFinite(inflBps)) return setError("Inflação inválida (ex.: 0,5).");
    const man = ["mc", "mb", "mo"].map((k) => percentToBps(String(f.get(k) || ""), 12_000));
    if (man.some((x) => x === null)) return setError("Aproveitamento manual inválido (0 a 120%).");
    if (!(man[0]! <= man[1]! && man[1]! <= man[2]!)) return setError("Use conservador ≤ base ≤ otimista.");
    setBusy(true);
    try {
      const r = await call<Result>("/api/v1/forecasts/run", { method: "POST", body: {
        horizon_months: Number(f.get("horizon")), floor_cents: floorC, expense_inflation_bps: inflBps,
        manual: { conservative_bps: man[0], base_bps: man[1], optimistic_bps: man[2] },
        use_seasonality: f.get("season") === "on", adjustments: adjs, save, name: save ? String(f.get("name") || "") || null : null } });
      setRes(r);
      if (save) { setNotice("Hipótese salva."); loadSaved(); }
    } catch (err) { setError(err instanceof ApiError ? err.message : "Falha ao projetar"); } finally { setBusy(false); }
  }

  function addAdj(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const inc = parseBRLToCents(String(f.get("inc") || "0")); const exp = parseBRLToCents(String(f.get("exp") || "0"));
    if (inc === null || exp === null) return setError("Valores do ajuste inválidos.");
    setAdjs((a) => [...a, { month: String(f.get("amonth")), label: String(f.get("alabel")), income_delta_cents: inc, expense_cents: exp }]);
    e.currentTarget.reset();
  }

  return (
    <>
      <h1>Previsões</h1>
      <p className="muted">Três cenários com premissas visíveis. É uma hipótese com faixa de incerteza, não uma promessa de resultado.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <form className="card" onSubmit={(e) => { e.preventDefault(); runWith(e.currentTarget, false); }}>
        <h2>Premissas</h2>
        <div className="grid2">
          <div><label htmlFor="horizon">Horizonte</label><select id="horizon" name="horizon" defaultValue="6"><option value="3">3 meses</option><option value="6">6 meses</option><option value="12">12 meses</option></select></div>
          <div><label htmlFor="floor">Piso de renda líquida mensal (R$, opcional)</label><input id="floor" name="floor" inputMode="decimal" placeholder="8.000,00" /></div>
          <div><label htmlFor="infl">Inflação das despesas (% ao mês)</label><input id="infl" name="infl" inputMode="decimal" defaultValue="0" /></div>
          <div><label htmlFor="season" style={{ fontWeight: 400 }}><input id="season" name="season" type="checkbox" /> Aplicar sazonalidade nos serviços (exige 12 meses de histórico)</label></div>
        </div>
        <fieldset style={{ border: 0, padding: 0 }}>
          <legend>Aproveitamento do plano quando há pouco histórico (%)</legend>
          <div className="grid2">
            <div><label htmlFor="mc">Conservador</label><input id="mc" name="mc" defaultValue="80" inputMode="decimal" /></div>
            <div><label htmlFor="mb">Base</label><input id="mb" name="mb" defaultValue="100" inputMode="decimal" /></div>
            <div><label htmlFor="mo">Otimista</label><input id="mo" name="mo" defaultValue="100" inputMode="decimal" /></div>
          </div>
        </fieldset>
        <p className="muted">Com 3 ou mais meses completos de realizado, o histórico substitui estas premissas.</p>
        <p>
          <button type="submit" disabled={busy}>{busy ? "Projetando…" : "Projetar"}</button>{" "}
          <input aria-label="Nome da hipótese" name="name" placeholder="Nome para salvar" style={{ width: "auto", display: "inline-block" }} />{" "}
          <button type="button" className="secondary" disabled={busy} onClick={(e) => e.currentTarget.form && runWith(e.currentTarget.form, true)}>Salvar hipótese</button>
        </p>
      </form>

      <form className="card" onSubmit={addAdj} style={{ marginTop: 16 }}>
        <h2>Eventos e ajustes manuais</h2>
        <div className="grid2">
          <div><label htmlFor="amonth">Mês</label><input id="amonth" name="amonth" type="month" required /></div>
          <div><label htmlFor="alabel">Descrição</label><input id="alabel" name="alabel" required maxLength={80} placeholder="Curso, equipamento, bônus…" /></div>
          <div><label htmlFor="inc">Receita extra (R$)</label><input id="inc" name="inc" inputMode="decimal" placeholder="0,00" /></div>
          <div><label htmlFor="exp">Despesa extra (R$)</label><input id="exp" name="exp" inputMode="decimal" placeholder="0,00" /></div>
        </div>
        <p><button type="submit" className="secondary">Adicionar ajuste</button></p>
        {adjs.length > 0 && <ul>{adjs.map((a, i) => <li key={i}>{a.month} · {a.label} · +{formatBRL(a.income_delta_cents)} / −{formatBRL(a.expense_cents)} <button type="button" className="secondary" onClick={() => setAdjs((x) => x.filter((_, j) => j !== i))} aria-label={`Remover ajuste ${a.label}`}>Remover</button></li>)}</ul>}
        <p className="muted">Feriados e férias vêm de Agenda &amp; Capacidade; despesas planejadas e recorrentes, de Receitas &amp; Despesas.</p>
      </form>

      {res && (
        <div className="card" style={{ marginTop: 16 }} role="region" aria-label="Resultado da previsão">
          <h2>Resultado líquido mensal previsto</h2>
          <p className="muted">{res.data_quality === "history" ? `Com histórico de ${res.assumptions.attainment.months_used} meses.` : "Com premissas manuais (pouco histórico)."}</p>
          <ForecastChart months={res.months} />
          <div className="tablewrap"><table>
            <thead><tr><th>Mês</th><th>Dias úteis</th><th>Conservador</th><th>Base</th><th>Otimista</th><th>Despesas</th><th>Meta</th></tr></thead>
            <tbody>{res.months.map((m) => (
              <tr key={m.month}>
                <td>{m.month}{m.flags.includes("SEM_PLANO") && <><br /><span className="muted">sem plano de renda</span></>}</td>
                <td>{m.working_days - m.working_days_lost}/{m.working_days}</td>
                <td>{formatBRL(m.net_cents.conservative)}</td><td>{formatBRL(m.net_cents.base)}</td><td>{formatBRL(m.net_cents.optimistic)}</td>
                <td>{formatBRL(m.expenses_cents)}</td><td>{m.goal_target_cents === null ? "—" : formatBRL(m.goal_target_cents)}</td>
              </tr>))}
              <tr><td><strong>Total</strong></td><td /><td>{formatBRL(res.totals.conservative)}</td><td>{formatBRL(res.totals.base)}</td><td>{formatBRL(res.totals.optimistic)}</td><td /><td /></tr>
            </tbody>
          </table></div>
          {res.reserve && (
            <p>Reserva para garantir o piso: <strong>{formatBRL(res.reserve.conservative.reserve_cents)}</strong> no pior caso ({res.reserve.conservative.months_below_floor} mês(es) abaixo do piso) e {formatBRL(res.reserve.base.reserve_cents)} no cenário base.</p>
          )}
          {res.months.some((m) => m.drivers.length > 0) && (
            <details open><summary>O que move cada mês</summary><ul>{res.months.flatMap((m) => m.drivers.map((d) => <li key={m.month + d}><strong>{m.month}:</strong> {d}</li>))}</ul></details>
          )}
          <details><summary>Premissas e limites</summary><ul>{res.assumptions.notes.map((n) => <li key={n}>{n}</li>)}</ul></details>
        </div>
      )}

      <h2>Hipóteses salvas</h2>
      {saved.length === 0 ? <p className="muted">Nenhuma hipótese salva.</p> : (
        <ul>{saved.map((s) => (
          <li key={s.id}>{s.name ?? "Sem nome"} · {s.from_month} a {s.to_month}{" "}
            <button className="secondary" onClick={() => call<Result>(`/api/v1/forecasts/${s.id}`).then(setRes)}>Abrir</button>{" "}
            <button className="secondary" aria-label={`Excluir hipótese ${s.name ?? s.id}`} onClick={() => call(`/api/v1/forecasts/${s.id}`, { method: "DELETE" }).then(loadSaved)}>Excluir</button></li>
        ))}</ul>
      )}
    </>
  );
}
