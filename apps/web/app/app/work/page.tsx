"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents } from "@/lib/money";
import { useSession } from "../session";

type Work = {
  id: string;
  name: string;
  work_type: string;
  regime: string;
  income: {
    gross_cents: number;
    effective_tax_bps: number;
    tax_rate_source: string; // INFORMADA | PADRAO | NENHUMA
    estimated_net_cents: number;
    net_per_committed_hour_cents: number | null;
  };
};

const TYPES: Record<string, string> = {
  CLINIC: "Consultório", SHIFT: "Plantão", ON_CALL: "Sobreaviso", EMPLOYMENT: "CLT",
  TELEMEDICINE: "Telemedicina", PROCEDURE: "Procedimentos", OTHER: "Outro",
};
const UNITS: Record<string, string> = {
  PER_EVENT: "Por evento", PER_CONSULT: "Por consulta", WEEKLY: "Semanal", MONTHLY: "Mensal",
};

export default function WorkPage() {
  const { me, org } = useSession();
  const [items, setItems] = useState<Work[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    api<Work[]>("/api/v1/work-sources", { orgId: org.id })
      .then(setItems)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Falha ao carregar"));
  }, [org.id]);
  useEffect(load, [load]);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    const form = e.currentTarget;
    const f = new FormData(form);
    const gross = parseBRLToCents(String(f.get("gross")));
    if (gross === null) return setError("Valor bruto inválido. Use o formato 2.000,00");
    const hours = Number(f.get("hours") || 0);
    setSaving(true);
    try {
      await api("/api/v1/work-sources", {
        method: "POST", csrf: me.csrf_token, orgId: org.id,
        body: {
          name: f.get("name"), work_type: f.get("work_type"), regime: f.get("regime"),
          income: {
            gross_cents: gross, unit_type: f.get("unit_type"),
            duration_minutes: Math.round(hours * 60),
            // em branco = "não informada": vale o padrão do regime, se habilitado em Tributos PF/PJ
            tax_rate_bps: String(f.get("tax") ?? "").trim() === "" ? null : Math.round(Number(f.get("tax")) * 100),
          },
        },
      });
      form.reset();
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h1>Trabalhos &amp; Rendas</h1>
      <form className="card" onSubmit={onSubmit}>
        <h2>Novo trabalho</h2>
        <div className="grid2">
          <div><label htmlFor="name">Nome</label><input id="name" name="name" required /></div>
          <div>
            <label htmlFor="work_type">Tipo</label>
            <select id="work_type" name="work_type">
              {Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="regime">Regime</label>
            <select id="regime" name="regime">
              <option>PF</option><option>PJ</option><option>CLT</option><option>OTHER</option>
            </select>
          </div>
          <div>
            <label htmlFor="unit_type">Periodicidade</label>
            <select id="unit_type" name="unit_type">
              {Object.entries(UNITS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div><label htmlFor="gross">Valor bruto (R$)</label><input id="gross" name="gross" inputMode="decimal" required placeholder="2.000,00" /></div>
          <div><label htmlFor="hours">Horas ativas</label><input id="hours" name="hours" type="number" min="0" step="0.5" /></div>
          <div><label htmlFor="tax">Retenção estimada (%)</label><input id="tax" name="tax" type="number" min="0" max="100" step="0.01" aria-describedby="tax-hint" /><small id="tax-hint" className="muted">Em branco: usa o padrão do regime, se habilitado em <Link href="/app/taxes">Tributos PF/PJ</Link>.</small></div>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <p><button type="submit" disabled={saving}>{saving ? "Salvando…" : "Salvar trabalho"}</button></p>
      </form>

      <h2>Cadastrados</h2>
      {items === null ? <p aria-busy="true">Carregando…</p> : items.length === 0 ? (
        <p className="muted">Nenhum trabalho ainda. Cadastre o primeiro acima.</p>
      ) : (
        <div className="tablewrap card">
          <table>
            <thead><tr><th>Nome</th><th>Tipo</th><th>Regime</th><th>Bruto</th><th>Retenção*</th><th>Líquido est.*</th><th>Líq./h comprometida*</th></tr></thead>
            <tbody>
              {items.map((w) => (
                <tr key={w.id}>
                  <td>{w.name}</td><td>{TYPES[w.work_type]}</td><td>{w.regime}</td>
                  <td>{formatBRL(w.income.gross_cents)}</td>
                  <td>{w.income.effective_tax_bps ? `${(w.income.effective_tax_bps / 100).toFixed(2).replace(".", ",")}%` : "—"}{w.income.tax_rate_source === "PADRAO" ? " (padrão)" : ""}</td>
                  <td>{formatBRL(w.income.estimated_net_cents)}</td>
                  <td>{w.income.net_per_committed_hour_cents === null ? "—" : formatBRL(w.income.net_per_committed_hour_cents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">* Estimativa com retenção informada por você; não é consultoria tributária.</p>
        </div>
      )}
    </>
  );
}
