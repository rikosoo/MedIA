"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents } from "@/lib/money";
import { useSession } from "@/app/app/session";

type SourceRow = {
  source_type: string; source_id: string; name: string; planned_units: number; realized_units: number;
  planned_gross_cents: number; realized_gross_cents: number; variance_gross_cents: number; variance_pct_bps: number | null;
  realized_net_estimate_cents: number | null; status: string;
};
type Compare = {
  phase: string; pace: { elapsed_pct_bps: number; realized_vs_planned_gross_pct_bps: number | null };
  sources: SourceRow[];
  totals: { planned_gross_cents: number; realized_gross_cents: number; unlinked_income_cents: number; planned_expenses_cents: number; realized_expenses_cents: number };
  expenses: { category: string; label: string; planned_cents: number; realized_cents: number }[];
  notes: string[];
};
type Plan = { source_type: string; source_id: string; name: string; eligible: boolean };

const STATUS: Record<string, string> = {
  NOT_STARTED: "Ainda não registrado", BEHIND: "Abaixo do planejado", ON_PLAN: "Conforme o plano", AHEAD: "Acima do planejado", UNPLANNED: "Sem plano", NONE: "—",
};
const pct = (bps: number | null) => (bps === null ? "—" : `${Math.round(bps / 100)}%`);

export function RealizedPanel({ month, onChanged }: { month: string; onChanged: () => void }) {
  const { me, org } = useSession();
  const [cmp, setCmp] = useState<Compare | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dup, setDup] = useState<Record<string, unknown> | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Compare>(`/api/v1/realized/compare?month=${month}`, { orgId: org.id }).then(setCmp).catch((e) => setError(e instanceof ApiError ? e.message : "Falha ao comparar"));
    api<Plan[]>(`/api/v1/income-plans?month=${month}`, { orgId: org.id }).then((p) => setPlans(p.filter((x) => x.eligible))).catch(() => setPlans([]));
  }, [month, org.id]);
  useEffect(load, [load]);

  async function post(body: Record<string, unknown>) {
    setBusy(true); setError(null); setNotice(null);
    try {
      await api("/api/v1/realized", { method: "POST", csrf: me.csrf_token, orgId: org.id, body });
      setDup(null); setNotice("Realizado registrado."); setOpen(false); load(); onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setDup(body); else setError(e instanceof ApiError ? e.message : "Falha ao registrar");
    } finally { setBusy(false); }
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const [type, id] = String(f.get("source")).split(":");
    const cents = parseBRLToCents(String(f.get("amount")));
    if (!id) return setError("Escolha a fonte.");
    if (cents === null || cents <= 0) return setError("Valor inválido. Use o formato 4.000,00");
    post({ source_type: type, source_id: id, month, units: Number(f.get("units")), amount_cents: cents, paid_at: f.get("paid_at"), note: String(f.get("note") || "") || null });
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2>Esperado × realizado</h2>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {cmp && (
        <>
          <p className="muted">
            {cmp.phase === "OPEN" ? `${pct(cmp.pace.elapsed_pct_bps)} do mês já passou` : cmp.phase === "CLOSED" ? "Mês encerrado" : "Mês futuro"}
            {cmp.pace.realized_vs_planned_gross_pct_bps !== null && ` · ${pct(cmp.pace.realized_vs_planned_gross_pct_bps)} do planejado já foi recebido`}
          </p>
          {cmp.sources.length === 0 ? <p className="muted">Nenhuma fonte planejada nem realizada neste mês.</p> : (
            <div className="tablewrap"><table>
              <thead><tr><th>Fonte</th><th>Unid. plan./real.</th><th>Planejado (bruto)</th><th>Realizado (bruto)</th><th>Diferença</th><th>Situação</th></tr></thead>
              <tbody>{cmp.sources.map((s) => (
                <tr key={s.source_type + s.source_id}>
                  <td>{s.name}</td><td>{s.planned_units} / {s.realized_units}</td><td>{formatBRL(s.planned_gross_cents)}</td>
                  <td>{formatBRL(s.realized_gross_cents)}{s.realized_net_estimate_cents !== null && <><br /><span className="muted">líq. est. {formatBRL(s.realized_net_estimate_cents)}</span></>}</td>
                  <td>{formatBRL(s.variance_gross_cents)}<br /><span className="muted">{pct(s.variance_pct_bps)} do plano</span></td><td>{STATUS[s.status]}</td>
                </tr>))}</tbody>
            </table></div>
          )}
          {cmp.totals.unlinked_income_cents !== 0 && <p className="muted">Receitas sem vínculo a uma fonte: {formatBRL(cmp.totals.unlinked_income_cents)} (vincule-as a um trabalho ou serviço para compará-las).</p>}
          {cmp.expenses.length > 0 && (
            <details><summary>Despesas: planejado × realizado</summary>
              <div className="tablewrap"><table>
                <thead><tr><th>Categoria</th><th>Planejado</th><th>Realizado</th></tr></thead>
                <tbody>{cmp.expenses.map((e) => <tr key={e.category}><td>{e.label}</td><td>{formatBRL(e.planned_cents)}</td><td>{formatBRL(e.realized_cents)}</td></tr>)}
                  <tr><td><strong>Total</strong></td><td>{formatBRL(cmp.totals.planned_expenses_cents)}</td><td>{formatBRL(cmp.totals.realized_expenses_cents)}</td></tr></tbody>
              </table></div>
            </details>
          )}
        </>
      )}

      <p><button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}>Atualizar realizado</button></p>
      {open && (
        <form onSubmit={onSubmit} aria-label="Registrar realizado">
          <div className="grid2">
            <div>
              <label htmlFor="rsrc">Fonte</label>
              <select id="rsrc" name="source" defaultValue="">
                <option value="" disabled>Escolha…</option>
                {plans.map((p) => <option key={p.source_id} value={`${p.source_type}:${p.source_id}`}>{p.name} ({p.source_type === "WORK" ? "trabalho" : "serviço"})</option>)}
              </select>
            </div>
            <div><label htmlFor="runits">Quantos (plantões / pacientes)</label><input id="runits" name="units" type="number" min="1" defaultValue={1} required /></div>
            <div><label htmlFor="ramount">Valor recebido (R$)</label><input id="ramount" name="amount" inputMode="decimal" required placeholder="4.000,00" /></div>
            <div><label htmlFor="rpaid">Data do recebimento</label><input id="rpaid" name="paid_at" type="date" required /></div>
            <div><label htmlFor="rnote">Observação (opcional)</label><input id="rnote" name="note" maxLength={120} /></div>
          </div>
          {dup && <p className="error" role="alert">Já existe um registro igual. É outro recebimento? <button type="button" className="secondary" onClick={() => post({ ...dup, allow_duplicate: true })}>Registrar mesmo assim</button></p>}
          <p><button type="submit" disabled={busy}>Registrar</button> <span className="muted">Só entra como realizado o que você registra aqui.</span></p>
        </form>
      )}
    </div>
  );
}
