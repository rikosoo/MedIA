"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, percentToBps } from "@/lib/money";
import { useSession } from "../session";

type Settings = { enabled: boolean; pf_bps: number; pj_bps: number; clinic_regime: string; reference: { pf_bps: number; pj_bps: number }; notes: string[] };
type Row = { name: string; kind: string; regime: string | null; tax_rate_bps: number; tax_source: string; units: number; gross_cents: number; tax_cents: number; net_cents: number };
type Summary = { enabled: boolean; rows: Row[]; total_gross_cents: number; total_tax_cents: number; uses_default: boolean };

const SOURCE: Record<string, string> = { INFORMADA: "informada por você", PADRAO: "padrão do regime", NENHUMA: "sem retenção" };
const pct = (bps: number) => (bps / 100).toFixed(2).replace(".", ",");
const thisMonth = () => new Date().toISOString().slice(0, 7);

export default function TaxesPage() {
  const { me, org } = useSession();
  const canEdit = org.role === "OWNER";
  const [s, setS] = useState<Settings | null>(null);
  const [form, setForm] = useState({ enabled: false, pf: "27", pj: "15", clinic: "PJ" });
  const [month, setMonth] = useState(thisMonth());
  const [sum, setSum] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  const fill = (x: Settings) => setForm({ enabled: x.enabled, pf: pct(x.pf_bps).replace(",", "."), pj: pct(x.pj_bps).replace(",", "."), clinic: x.clinic_regime });
  const loadSettings = useCallback(() => { call<Settings>("/api/v1/tax/settings").then((x) => { setS(x); fill(x); }).catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? "Você não tem permissão para ver os tributos." : e.message) : "Falha ao carregar")); }, [call]);
  const loadSummary = useCallback(() => { call<Summary>(`/api/v1/tax/summary?month=${month}`).then(setSum).catch(() => setSum(null)); }, [call, month]);
  useEffect(loadSettings, [loadSettings]);
  useEffect(loadSummary, [loadSummary]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setNotice(null);
    const pf = percentToBps(form.pf), pj = percentToBps(form.pj);
    if (pf === null || pj === null) return setError("Alíquota inválida. Use um valor de 0 a 100 (ex.: 27 ou 15,5).");
    setBusy(true);
    try {
      const r = await call<Settings>("/api/v1/tax/settings", { method: "PUT", body: { enabled: form.enabled, pf_bps: pf, pj_bps: pj, clinic_regime: form.clinic } });
      setS(r); fill(r); loadSummary();
      setNotice(r.enabled ? "Salvo. As alíquotas padrão agora entram nos cálculos." : "Salvo. As alíquotas padrão estão desligadas: nada muda nos cálculos.");
    } catch (err) { setError(err instanceof ApiError ? err.message : "Falha ao salvar"); } finally { setBusy(false); }
  }

  return (
    <div>
      <h1>Tributos PF/PJ</h1>
      <p className="muted">Alíquotas médias de referência para estimar a retenção quando você não informa uma. <strong>São estimativas, não tributos apurados</strong>: confirme com seu contador.</p>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      {s && (
        <form className="card" onSubmit={save} aria-labelledby="t-title">
          <h2 id="t-title">Alíquotas padrão</h2>
          <label htmlFor="en"><input id="en" type="checkbox" checked={form.enabled} disabled={!canEdit} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Usar estas alíquotas nos cálculos</label>
          <div className="grid2">
            <div><label htmlFor="pf">Pessoa Física — PF (%)</label><input id="pf" inputMode="decimal" value={form.pf} disabled={!canEdit} onChange={(e) => setForm({ ...form, pf: e.target.value })} /></div>
            <div><label htmlFor="pj">Pessoa Jurídica — PJ (%)</label><input id="pj" inputMode="decimal" value={form.pj} disabled={!canEdit} onChange={(e) => setForm({ ...form, pj: e.target.value })} /></div>
          </div>
          <label htmlFor="cl">Regime da renda do consultório</label>
          <select id="cl" value={form.clinic} disabled={!canEdit} onChange={(e) => setForm({ ...form, clinic: e.target.value })}>
            <option value="PJ">PJ (empresa)</option>
            <option value="PF">PF (pessoa física)</option>
          </select>
          <ul className="muted">{s.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          {canEdit ? (
            <p>
              <button type="submit" disabled={busy}>{busy ? "Salvando…" : "Salvar"}</button>{" "}
              <button type="button" className="secondary" onClick={() => setForm({ ...form, pf: pct(s.reference.pf_bps).replace(",", "."), pj: pct(s.reference.pj_bps).replace(",", "."), clinic: "PJ" })}>Restaurar médias (PF {pct(s.reference.pf_bps)}% · PJ {pct(s.reference.pj_bps)}%)</button>
            </p>
          ) : <p className="muted">Só o proprietário da organização altera estas alíquotas.</p>}
        </form>
      )}

      <section className="card" aria-labelledby="t-eff">
        <h2 id="t-eff">Efeito no mês</h2>
        <label htmlFor="month">Competência</label>
        <input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} style={{ maxWidth: 220 }} />
        {!sum ? <p aria-busy="true">Carregando…</p> : sum.rows.length === 0 ? <p className="muted">Nenhuma renda planejada neste mês. Planeje em Minha Meta.</p> : (
          <>
            <div className="tablewrap">
              <table>
                <thead><tr><th>Renda</th><th>Regime</th><th>Alíquota</th><th>Origem</th><th>Bruto</th><th>Retenção estimada</th><th>Líquido est.</th></tr></thead>
                <tbody>
                  {sum.rows.map((r) => (
                    <tr key={r.name}>
                      <td>{r.name}</td><td>{r.regime ?? "—"}</td><td>{pct(r.tax_rate_bps)}%</td><td>{SOURCE[r.tax_source]}</td>
                      <td>{formatBRL(r.gross_cents)}</td><td>{formatBRL(r.tax_cents)}</td><td>{formatBRL(r.net_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>Total do mês: bruto <strong>{formatBRL(sum.total_gross_cents)}</strong> · retenção estimada <strong>{formatBRL(sum.total_tax_cents)}</strong>.</p>
            {!sum.enabled && <p className="muted">Alíquotas padrão desligadas: só as retenções que você informou entram na conta.</p>}
          </>
        )}
      </section>
    </div>
  );
}
