"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL } from "@/lib/money";
import { useSession } from "../session";

type Plan = { plan: string; name: string; price_cents: number; summary: string };
type Sub = {
  plan: string; plan_name: string; effective_plan: string; status: string; in_trial: boolean; trial_ends_at: string;
  next_billing_at: string | null; current_period_end: string | null; cancel_at_period_end: boolean; pending_plan: string | null;
  grace_ends_at: string | null; payment_method: { brand: string; last4: string } | null; price_cents: number;
  limits: Record<string, number | null>; usage: Record<string, number>; provider_note: string;
};
type Preview = { action: string; charge_today_cents: number; message: string };
type Invoice = { id: string; number: string; description: string; formatted: string; status: string; created_at: string };

const STATUS: Record<string, string> = {
  TRIALING: "Teste grátis", FREE: "Gratuito", ACTIVE: "Ativa", CANCELING: "Cancelamento agendado", PAST_DUE: "Pagamento pendente",
};
const LIMITS: Record<string, string> = {
  works: "Trabalhos", goals_per_month: "Metas no mês", optimizer_runs_per_month: "Simulações no mês", assistant_per_day: "Perguntas ao consultor hoje",
};
const TOKENS = [
  ["tok_visa", "Visa final 4242"], ["tok_mastercard", "Mastercard final 4444"],
  ["tok_fail", "Cartão recusado (teste)"], ["tok_fail_renewal", "Recusado só na renovação (teste)"],
];
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("pt-BR") : "—");

export default function BillingPage() {
  const { me, org } = useSession();
  const [sub, setSub] = useState<Sub | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [preview, setPreview] = useState<(Preview & { plan: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const idem = useRef<string>("");
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown; headers?: Record<string, string> } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  const load = useCallback(async () => {
    try {
      const [s, p, i] = await Promise.all([call<Sub>("/api/v1/billing/subscription"), call<Plan[]>("/api/v1/billing/plans"), call<Invoice[]>("/api/v1/billing/invoices")]);
      setSub(s); setPlans(p); setInvoices(i);
    } catch (e) { setError(e instanceof ApiError ? (e.status === 403 ? "Só o proprietário da organização gerencia a cobrança." : e.message) : "Falha ao carregar"); }
  }, [call]);
  useEffect(() => { void load(); }, [load]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); setNotice(ok); setPreview(null); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Falha na operação"); } finally { setBusy(false); }
  }

  async function ask(plan: string) {
    setError(null); setNotice(null);
    try {
      const p = await call<Preview>("/api/v1/billing/subscription/preview", { method: "POST", body: { plan } });
      idem.current = crypto.randomUUID();
      setPreview({ ...p, plan });
    } catch (e) { setError(e instanceof ApiError ? e.message : "Falha ao calcular"); }
  }

  async function exportData() {
    setError(null);
    try {
      const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
      const res = await fetch(`${BASE}/api/v1/billing/export`, { credentials: "include", headers: { "X-Organization-Id": org.id } });
      if (!res.ok) throw new Error();
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a"); a.href = url; a.download = "financemed-dados.json"; a.click(); URL.revokeObjectURL(url);
      setNotice("Exportação gerada.");
    } catch { setError("Não foi possível exportar."); }
  }

  if (!sub) return <div><h1>Planos &amp; Cobrança</h1>{error ? <p role="alert" className="error">{error}</p> : <p aria-busy="true">Carregando…</p>}</div>;

  return (
    <div>
      <h1>Planos &amp; Cobrança</h1>
      <p className="muted">{sub.provider_note}</p>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {sub.status === "PAST_DUE" && (
        <p role="alert" className="error">O último pagamento falhou. Atualize a forma de pagamento até {day(sub.grace_ends_at)} para não voltar ao plano Gratuito.</p>
      )}

      <div className="card">
        <h2>Sua assinatura</h2>
        <p>Plano <strong>{sub.plan_name}</strong> · {STATUS[sub.status] ?? sub.status}{sub.price_cents > 0 ? ` · ${formatBRL(sub.price_cents)}/mês` : ""}</p>
        {sub.in_trial && <p>Teste grátis com todos os recursos até <strong>{day(sub.trial_ends_at)}</strong>. Depois disso, sem assinatura, você volta ao plano Gratuito (nada é cobrado sem você assinar).</p>}
        {sub.next_billing_at && <p>Próxima cobrança em {day(sub.next_billing_at)}.</p>}
        {sub.cancel_at_period_end && <p>Cancelamento agendado: acesso até {day(sub.current_period_end)}.</p>}
        {sub.pending_plan && <p>Mudança agendada para o plano {plans.find((p) => p.plan === sub.pending_plan)?.name} em {day(sub.current_period_end)}.</p>}
        {(sub.cancel_at_period_end || sub.pending_plan) && (
          <button type="button" className="secondary" disabled={busy} onClick={() => void run(() => call("/api/v1/billing/resume", { method: "POST" }), "Mudança desfeita.")}>Manter meu plano atual</button>
        )}
        <h3>Uso do plano</h3>
        <ul>
          {Object.entries(sub.limits).filter(([k]) => k in LIMITS).map(([k, v]) => (
            <li key={k}>{LIMITS[k]}: {sub.usage[k] ?? 0} {v === null ? "(sem limite)" : `de ${v}`}</li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h2>Planos</h2>
        <div className="grid2">
          {plans.map((p) => (
            <section key={p.plan} aria-label={`Plano ${p.name}`}>
              <h3>{p.name} <span className="muted">{p.price_cents ? `${formatBRL(p.price_cents)}/mês` : "R$ 0"}</span></h3>
              <p>{p.summary}</p>
              {p.plan === sub.plan && !sub.in_trial ? <p><strong>Plano atual</strong></p> : (
                <button type="button" className="secondary" disabled={busy} onClick={() => void ask(p.plan)}>{p.plan === "FREE" ? "Voltar ao Gratuito" : "Alterar plano"}<span className="sr-only"> para {p.name}</span></button>
              )}
            </section>
          ))}
        </div>
        <p className="muted"><small>Valores hipotéticos, em validação. O app não promete aumento de faturamento. Exportação dos seus dados é possível em qualquer plano.</small></p>
        {preview && (
          <div role="region" aria-label="Confirmar mudança de plano" className="card">
            <h3>Confirmar mudança</h3>
            <p>{preview.message}</p>
            <p><strong>Cobrança hoje: {formatBRL(preview.charge_today_cents)}</strong></p>
            <button type="button" disabled={busy} onClick={() => void run(async () => {
              await call("/api/v1/billing/subscription", { method: "PATCH", body: { plan: preview.plan }, headers: { "Idempotency-Key": idem.current } });
            }, "Plano atualizado.")}>Confirmar</button>{" "}
            <button type="button" className="secondary" onClick={() => setPreview(null)}>Voltar</button>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Forma de pagamento</h2>
        <p>{sub.payment_method ? `${sub.payment_method.brand} final ${sub.payment_method.last4}` : "Nenhuma cadastrada."}</p>
        <form onSubmit={(e) => { e.preventDefault(); const token = String(new FormData(e.currentTarget).get("token")); void run(() => call("/api/v1/billing/payment-method", { method: "PUT", body: { token } }), "Forma de pagamento atualizada."); }}>
          <label htmlFor="token">Cartão (ambiente de teste: sem cobrança real)</label>
          <select id="token" name="token">{TOKENS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <button type="submit" disabled={busy}>Atualizar pagamento</button>
        </form>
        <p className="muted"><small>O app nunca recebe o número do cartão: só um token do provedor de pagamento.</small></p>
      </div>

      <div className="card">
        <h2>Faturas</h2>
        {invoices.length === 0 ? <p className="muted">Nenhuma fatura ainda.</p> : (
          <div className="tablewrap"><table>
            <thead><tr><th>Número</th><th>Descrição</th><th>Valor</th><th>Situação</th><th>Data</th></tr></thead>
            <tbody>{invoices.map((i) => <tr key={i.id}><td>{i.number}</td><td>{i.description}</td><td>{i.formatted}</td><td>{i.status === "PAID" ? "Paga" : "Falhou"}</td><td>{day(i.created_at)}</td></tr>)}</tbody>
          </table></div>
        )}
      </div>

      <div className="card">
        <h2>Seus dados e cancelamento</h2>
        <p><button type="button" className="secondary" onClick={() => void exportData()}>Exportar meus dados (JSON)</button></p>
        {sub.plan !== "FREE" && !sub.cancel_at_period_end && sub.pending_plan !== "FREE" && (
          <div>
            <p>Cancelar mantém o acesso até o fim do período pago ({day(sub.current_period_end)}), sem nova cobrança e sem reembolso do período corrente. Seus dados e a exportação continuam disponíveis.</p>
            <label htmlFor="conf"><input id="conf" type="checkbox" checked={confirmCancel} onChange={(e) => setConfirmCancel(e.target.checked)} /> Entendo e quero cancelar a assinatura</label>
            <button type="button" disabled={busy || !confirmCancel} onClick={() => void run(async () => { await call("/api/v1/billing/cancel", { method: "POST", body: { confirm: true } }); setConfirmCancel(false); }, "Cancelamento agendado.")}>Cancelar assinatura</button>
          </div>
        )}
      </div>
    </div>
  );
}
