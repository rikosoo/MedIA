"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL, parseBRLToCents } from "@/lib/money";
import { useSession } from "../session";

type Tx = {
  id: string; type: "INCOME" | "EXPENSE"; status: "PLANNED" | "REALIZED"; competence_month: string;
  paid_at: string | null; signed_cents: number; category: string; description: string; is_reversal: boolean;
};
type Summary = {
  competence: {
    income_realized_cents: number; expense_realized_cents: number; result_realized_cents: number;
    expense_planned_cents: number; income_planned_cents: number;
  };
  cash: { income_received_cents: number; expense_paid_cents: number; net_cents: number };
};
type Cats = { expense: Record<string, string>; income: Record<string, string> };

const thisMonth = () => new Date().toISOString().slice(0, 7);

function Result({ cents }: { cents: number }) {
  // Cor nunca é o único sinal: o rótulo diz "Déficit" / "Superávit".
  const label = cents < 0 ? "Déficit" : cents > 0 ? "Superávit" : "Zerado";
  return <strong style={{ color: cents < 0 ? "var(--err)" : cents > 0 ? "var(--ok)" : undefined }}>{label}: {formatBRL(cents)}</strong>;
}

export default function FinancesPage() {
  const { me, org } = useSession();
  const [month, setMonth] = useState(thisMonth());
  const [txs, setTxs] = useState<Tx[] | null>(null);
  const [sum, setSum] = useState<Summary | null>(null);
  const [cats, setCats] = useState<Cats | null>(null);
  const [type, setType] = useState<"EXPENSE" | "INCOME">("EXPENSE");
  const [status, setStatus] = useState<"REALIZED" | "PLANNED">("REALIZED");
  const [error, setError] = useState<string | null>(null);
  const [dup, setDup] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [t, s, c] = await Promise.all([
        api<Tx[]>(`/api/v1/transactions?month=${month}`, { orgId: org.id }),
        api<Summary>(`/api/v1/finances/summary?month=${month}`, { orgId: org.id }),
        api<Cats>("/api/v1/finance/categories", { orgId: org.id }),
      ]);
      setTxs(t); setSum(s); setCats(c);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha ao carregar");
    }
  }, [month, org.id]);
  useEffect(() => { load(); }, [load]);

  async function save(body: Record<string, unknown>) {
    setBusy(true); setError(null);
    try {
      await api("/api/v1/transactions", { method: "POST", csrf: me.csrf_token, orgId: org.id, body });
      setDup(null);
      await load();
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setDup(body);
      else setError(e instanceof ApiError ? e.message : "Falha ao salvar");
      return false;
    } finally { setBusy(false); }
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const cents = parseBRLToCents(String(f.get("amount")));
    if (cents === null || cents <= 0) return setError("Valor inválido. Use o formato 2.500,00");
    const body = {
      type, status, competence_month: f.get("competence"), amount_cents: cents,
      category: f.get("category"), description: f.get("description"),
      is_reversal: f.get("reversal") === "on", paid_at: status === "REALIZED" ? f.get("paid_at") : null,
    };
    if (await save(body)) form.reset();
  }

  async function remove(id: string) {
    if (!confirm("Excluir este lançamento?")) return;
    try {
      await api(`/api/v1/transactions/${id}`, { method: "DELETE", csrf: me.csrf_token, orgId: org.id });
      await load();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Falha ao excluir"); }
  }

  const catList = cats ? (type === "EXPENSE" ? cats.expense : cats.income) : {};

  return (
    <>
      <h1>Receitas &amp; Despesas</h1>
      <label htmlFor="month">Competência</label>
      <input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} style={{ maxWidth: 220 }} />
      {error && <p className="error" role="alert">{error}</p>}

      {sum && (
        <div className="grid2" style={{ marginTop: 16 }}>
          <div className="card">
            <h2>Competência</h2>
            <p>Receita realizada: {formatBRL(sum.competence.income_realized_cents)}</p>
            <p>Despesa realizada: {formatBRL(sum.competence.expense_realized_cents)}</p>
            <p><Result cents={sum.competence.result_realized_cents} /></p>
            <p className="muted">Despesa planejada (não é dinheiro gasto): {formatBRL(sum.competence.expense_planned_cents)}</p>
          </div>
          <div className="card">
            <h2>Caixa (por data de pagamento)</h2>
            <p>Recebido: {formatBRL(sum.cash.income_received_cents)}</p>
            <p>Pago: {formatBRL(sum.cash.expense_paid_cents)}</p>
            <p><Result cents={sum.cash.net_cents} /></p>
          </div>
        </div>
      )}

      <form className="card" onSubmit={onSubmit} style={{ marginTop: 16 }}>
        <h2>Novo lançamento</h2>
        <div className="grid2">
          <div>
            <label htmlFor="type">Tipo</label>
            <select id="type" value={type} onChange={(e) => setType(e.target.value as "EXPENSE" | "INCOME")}>
              <option value="EXPENSE">Despesa</option><option value="INCOME">Receita</option>
            </select>
          </div>
          <div>
            <label htmlFor="status">Situação</label>
            <select id="status" value={status} onChange={(e) => setStatus(e.target.value as "REALIZED" | "PLANNED")}>
              <option value="REALIZED">Realizado (já pago/recebido)</option><option value="PLANNED">Planejado</option>
            </select>
          </div>
          <div>
            <label htmlFor="category">Categoria</label>
            <select id="category" name="category" key={type}>
              {Object.entries(catList).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div><label htmlFor="description">Descrição</label><input id="description" name="description" required maxLength={200} /></div>
          <div><label htmlFor="amount">Valor (R$)</label><input id="amount" name="amount" inputMode="decimal" required placeholder="2.500,00" /></div>
          <div><label htmlFor="competence">Competência</label><input id="competence" name="competence" type="month" defaultValue={month} required /></div>
          {status === "REALIZED" && (
            <div><label htmlFor="paid_at">Data de pagamento/recebimento</label><input id="paid_at" name="paid_at" type="date" required /></div>
          )}
          <div><label htmlFor="reversal"><input id="reversal" name="reversal" type="checkbox" style={{ width: "auto", minHeight: 0 }} /> É um estorno</label></div>
        </div>
        {dup && (
          <p className="error" role="alert">
            Já existe um lançamento parecido. É realmente outro?{" "}
            <button type="button" className="secondary" onClick={() => save({ ...dup, allow_duplicate: true })}>Salvar mesmo assim</button>
          </p>
        )}
        <p><button type="submit" disabled={busy}>{busy ? "Salvando…" : "Salvar lançamento"}</button></p>
      </form>

      <h2>Lançamentos de {month}</h2>
      {txs === null ? <p aria-busy="true">Carregando…</p> : txs.length === 0 ? (
        <p className="muted">Nenhum lançamento neste mês.</p>
      ) : (
        <div className="tablewrap card">
          <table>
            <thead><tr><th>Descrição</th><th>Tipo</th><th>Situação</th><th>Efeito no resultado</th><th /></tr></thead>
            <tbody>
              {txs.map((t) => (
                <tr key={t.id}>
                  <td>{t.description}<br /><span className="muted">{cats?.[t.type === "EXPENSE" ? "expense" : "income"][t.category] ?? t.category}{t.paid_at ? ` · pago em ${t.paid_at}` : ""}</span></td>
                  <td>{t.type === "EXPENSE" ? "Despesa" : "Receita"}{t.is_reversal ? " (estorno)" : ""}</td>
                  <td>{t.status === "REALIZED" ? "Realizado" : "Planejado"}</td>
                  <td>{formatBRL(t.signed_cents)}</td>
                  <td><button className="secondary" onClick={() => remove(t.id)} aria-label={`Excluir ${t.description}`}>Excluir</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
