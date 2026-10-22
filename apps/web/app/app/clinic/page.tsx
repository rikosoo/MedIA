"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { bpsToPercent, formatBRL, parseBRLToCents, percentToBps } from "@/lib/money";
import { useSession } from "../session";

type Policy = { id: string; name: string; occurrence_rate_bps: number; delay_days: number; price_mode: string };
type Service = { id: string; name: string; price_cents: number; duration_minutes: number; return_policy_id: string | null };
type Econ = {
  expected_minutes: string; gross_revenue_cents: number; revenue_returns_cents: number;
  margin_cents: number; margin_per_hour_cents: number | null; price_cents: number;
};
type EconResp = { current: Econ; simulated: Econ | null; delta_margin_cents: number | null; assumptions: string[] };

const PRICE_MODES: Record<string, string> = { FREE: "Gratuito", PARTIAL: "Parcial", FULL: "Integral", CUSTOM: "Personalizado" };

export default function ClinicPage() {
  const { me, org } = useSession();
  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [services, setServices] = useState<Service[] | null>(null);
  const [econ, setEcon] = useState<Record<string, EconResp>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [priceMode, setPriceMode] = useState("FREE");
  const [durMode, setDurMode] = useState("PERCENT");
  const [sim, setSim] = useState<{ id: string; price: string } | null>(null);
  const [simResult, setSimResult] = useState<EconResp | null>(null);
  const [cap, setCap] = useState<{ id: string; hours: string; result?: number; minutes?: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([
        api<Policy[]>("/api/v1/return-policies", { orgId: org.id }),
        api<Service[]>("/api/v1/clinic-services", { orgId: org.id }),
      ]);
      setPolicies(p);
      setServices(s);
      const entries = await Promise.all(
        s.map(async (x) => [x.id, await api<EconResp>(`/api/v1/clinic-services/${x.id}/economics`, { orgId: org.id })] as const),
      );
      setEcon(Object.fromEntries(entries));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha ao carregar");
    }
  }, [org.id]);
  useEffect(() => { load(); }, [load]);

  async function submit<T>(fn: () => Promise<T>) {
    setError(null);
    setBusy(true);
    try { await fn(); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Falha ao salvar"); } finally { setBusy(false); }
  }

  function onPolicy(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const rate = percentToBps(String(f.get("rate")));
    if (rate === null) return setError("Taxa de retorno inválida (0 a 100%).");
    const body: Record<string, unknown> = {
      name: f.get("name"), occurrence_rate_bps: rate, delay_days: Number(f.get("delay")),
      duration_mode: durMode, price_mode: priceMode,
    };
    if (durMode === "PERCENT") {
      const d = percentToBps(String(f.get("dur")));
      if (d === null) return setError("Duração do retorno inválida (0 a 100%).");
      body.duration_percent_bps = d;
    } else body.duration_minutes = Number(f.get("dur"));
    if (priceMode === "PARTIAL") {
      const p = percentToBps(String(f.get("price")));
      if (p === null) return setError("Percentual do retorno inválido.");
      body.price_percent_bps = p;
    } else if (priceMode === "CUSTOM") {
      const c = parseBRLToCents(String(f.get("price")));
      if (c === null) return setError("Valor do retorno inválido.");
      body.price_cents = c;
    }
    if (f.get("free")) body.free_within_days = Number(f.get("free"));
    submit(async () => { await api("/api/v1/return-policies", { method: "POST", csrf: me.csrf_token, orgId: org.id, body }); form.reset(); });
  }

  function onService(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const price = parseBRLToCents(String(f.get("price")));
    const cost = parseBRLToCents(String(f.get("cost") || "0"));
    const fee = percentToBps(String(f.get("fee") || "0"));
    const ns = percentToBps(String(f.get("ns") || "0"));
    if (price === null || cost === null) return setError("Valores monetários inválidos. Use o formato 300,00");
    if (fee === null || ns === null) return setError("Percentuais inválidos (0 a 100%).");
    submit(async () => {
      await api("/api/v1/clinic-services", {
        method: "POST", csrf: me.csrf_token, orgId: org.id,
        body: {
          name: f.get("name"), price_cents: price, duration_minutes: Number(f.get("minutes")),
          cost_cents: cost, fee_bps: fee, no_show_rate_bps: ns, return_policy_id: f.get("policy") || null,
        },
      });
      form.reset();
    });
  }

  async function runSim() {
    if (!sim) return;
    const cents = parseBRLToCents(sim.price);
    if (cents === null) return setError("Preço simulado inválido.");
    setError(null);
    try { setSimResult(await api<EconResp>(`/api/v1/clinic-services/${sim.id}/economics?price_cents=${cents}`, { orgId: org.id })); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Falha na simulação"); }
  }

  async function runCap() {
    if (!cap) return;
    const minutes = Math.round(Number(cap.hours.replace(",", ".")) * 60);
    if (!Number.isFinite(minutes) || minutes < 0) return setError("Horas disponíveis inválidas.");
    setError(null);
    try {
      const r = await api<{ capacity_new_patients: number; expected_minutes_per_new_patient: string }>("/api/v1/clinic/capacity", {
        method: "POST", csrf: me.csrf_token, orgId: org.id, body: { service_id: cap.id, available_minutes: minutes },
      });
      setCap({ ...cap, result: r.capacity_new_patients, minutes: r.expected_minutes_per_new_patient });
    } catch (e) { setError(e instanceof ApiError ? e.message : "Falha no cálculo"); }
  }

  const policyName = (id: string | null) => policies?.find((p) => p.id === id)?.name ?? "Sem retorno";

  return (
    <>
      <h1>Consultório</h1>
      {error && <p className="error" role="alert">{error}</p>}

      <form className="card" onSubmit={onPolicy}>
        <h2>Política de retorno</h2>
        <div className="grid2">
          <div><label htmlFor="pname">Nome</label><input id="pname" name="name" required /></div>
          <div><label htmlFor="rate">Pacientes que retornam (%)</label><input id="rate" name="rate" inputMode="decimal" required placeholder="76" /></div>
          <div><label htmlFor="delay">Prazo do retorno (dias)</label><input id="delay" name="delay" type="number" min="0" max="365" required defaultValue={30} /></div>
          <div><label htmlFor="free">Gratuito se dentro de (dias, opcional)</label><input id="free" name="free" type="number" min="0" max="365" /></div>
          <div>
            <label htmlFor="dmode">Duração do retorno</label>
            <select id="dmode" value={durMode} onChange={(e) => setDurMode(e.target.value)}>
              <option value="PERCENT">% da consulta</option><option value="MINUTES">Minutos fixos</option>
            </select>
          </div>
          <div><label htmlFor="dur">{durMode === "PERCENT" ? "% da duração da consulta" : "Minutos"}</label><input id="dur" name="dur" inputMode="decimal" required placeholder={durMode === "PERCENT" ? "50" : "20"} /></div>
          <div>
            <label htmlFor="pmode">Cobrança do retorno</label>
            <select id="pmode" value={priceMode} onChange={(e) => setPriceMode(e.target.value)}>
              {Object.entries(PRICE_MODES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          {(priceMode === "PARTIAL" || priceMode === "CUSTOM") && (
            <div><label htmlFor="pr">{priceMode === "PARTIAL" ? "% do valor da consulta" : "Valor (R$)"}</label><input id="pr" name="price" inputMode="decimal" required /></div>
          )}
        </div>
        <p><button type="submit" disabled={busy}>Salvar política</button></p>
      </form>

      <form className="card" onSubmit={onService} style={{ marginTop: 16 }}>
        <h2>Novo serviço</h2>
        <div className="grid2">
          <div><label htmlFor="sname">Nome</label><input id="sname" name="name" required /></div>
          <div><label htmlFor="sprice">Preço (R$)</label><input id="sprice" name="price" inputMode="decimal" required placeholder="300,00" /></div>
          <div><label htmlFor="minutes">Duração (min)</label><input id="minutes" name="minutes" type="number" min="1" max="1440" required /></div>
          <div><label htmlFor="cost">Custo variável (R$)</label><input id="cost" name="cost" inputMode="decimal" placeholder="0,00" /></div>
          <div><label htmlFor="fee">Taxa de pagamento (%)</label><input id="fee" name="fee" inputMode="decimal" placeholder="0" /></div>
          <div><label htmlFor="ns">Faltas em consultas novas (%)</label><input id="ns" name="ns" inputMode="decimal" placeholder="0" /></div>
          <div>
            <label htmlFor="policy">Política de retorno</label>
            <select id="policy" name="policy" defaultValue="">
              <option value="">Sem retorno</option>
              {policies?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        </div>
        <p><button type="submit" disabled={busy}>Salvar serviço</button></p>
      </form>

      <h2>Serviços</h2>
      {services === null ? <p aria-busy="true">Carregando…</p> : services.length === 0 ? (
        <p className="muted">Nenhum serviço cadastrado. Crie uma política de retorno (opcional) e o primeiro serviço.</p>
      ) : (
        <div className="tablewrap card">
          <table>
            <thead><tr><th>Serviço</th><th>Retorno</th><th>Min. esperados</th><th>Receita bruta*</th><th>Margem*</th><th>Margem/h*</th><th>Ações</th></tr></thead>
            <tbody>
              {services.map((s) => {
                const c = econ[s.id]?.current;
                return (
                  <tr key={s.id}>
                    <td>{s.name}<br /><span className="muted">{formatBRL(s.price_cents)} · {s.duration_minutes} min</span></td>
                    <td>{policyName(s.return_policy_id)}</td>
                    <td>{c ? c.expected_minutes.replace(".", ",") : "—"}</td>
                    <td>{c ? formatBRL(c.gross_revenue_cents) : "—"}</td>
                    <td>{c ? formatBRL(c.margin_cents) : "—"}</td>
                    <td>{c?.margin_per_hour_cents == null ? "—" : formatBRL(c.margin_per_hour_cents)}</td>
                    <td>
                      <button className="secondary" onClick={() => { setSim({ id: s.id, price: "" }); setSimResult(null); }}>Simular preço</button>{" "}
                      <button className="secondary" onClick={() => setCap({ id: s.id, hours: "" })}>Capacidade</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted">* Por consulta nova agendada, com retornos no mesmo horizonte; antes de tributos e despesas fixas.</p>
        </div>
      )}

      {sim && (
        <div className="card" style={{ marginTop: 16 }} role="region" aria-label="Simulação de preço">
          <h2>Simular preço</h2>
          <label htmlFor="simp">Novo preço (R$)</label>
          <input id="simp" value={sim.price} onChange={(e) => setSim({ ...sim, price: e.target.value })} inputMode="decimal" />
          <p><button onClick={runSim}>Calcular</button></p>
          {simResult?.simulated && (
            <>
              <p>
                Margem por consulta: {formatBRL(simResult.current.margin_cents)} → {formatBRL(simResult.simulated.margin_cents)}{" "}
                (<strong>{(simResult.delta_margin_cents ?? 0) >= 0 ? "ganho" : "perda"} de {formatBRL(Math.abs(simResult.delta_margin_cents ?? 0))}</strong>)
              </p>
              <ul className="muted">{simResult.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            </>
          )}
        </div>
      )}

      {cap && (
        <div className="card" style={{ marginTop: 16 }} role="region" aria-label="Capacidade">
          <h2>Capacidade de pacientes novos</h2>
          <label htmlFor="caph">Horas disponíveis no período</label>
          <input id="caph" value={cap.hours} onChange={(e) => setCap({ ...cap, hours: e.target.value, result: undefined })} inputMode="decimal" />
          <p><button onClick={runCap}>Calcular</button></p>
          {cap.result !== undefined && (
            <p>
              Cabem cerca de <strong>{cap.result}</strong> pacientes novos ({cap.minutes?.replace(".", ",")} min cada, incluindo retornos).{" "}
              <span className="muted">Aproximação: a agenda real é calculada por slots (próxima sprint).</span>
            </p>
          )}
        </div>
      )}
      <p className="muted" style={{ marginTop: 16 }}>Taxas de retorno atuais: {policies?.map((p) => `${p.name} ${bpsToPercent(p.occurrence_rate_bps)} em ${p.delay_days}d`).join("; ") || "nenhuma"}.</p>
    </>
  );
}
