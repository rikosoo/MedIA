"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ReportView, type ReportDoc } from "@/components/ReportView";
import { api, ApiError } from "@/lib/api";
import { downloadFile, publicUrl } from "@/lib/download";
import { useSession } from "../session";

type Template = { template: string; name: string; period: "month" | "year"; description: string; allowed: boolean };
type Report = { id: string; template_name: string; period: string; format: string; filename: string; size_bytes: number; expires_at: string };
type Share = { id: string; expires_at: string; revoked: boolean; access_count: number; last_access_at: string | null };
type Closing = { month: string; phase: string; done: number; total: number; ready_to_close: boolean; items: { code: string; label: string; done: boolean; detail: string; route: string }[] };

const thisMonth = () => new Date().toISOString().slice(0, 7);

export default function ReportsPage() {
  const { me, org } = useSession();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [tpl, setTpl] = useState("MONTHLY_RESULT");
  const [month, setMonth] = useState(thisMonth());
  const [doc, setDoc] = useState<ReportDoc | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [shares, setShares] = useState<Record<string, Share[]>>({});
  const [link, setLink] = useState<{ id: string; url: string } | null>(null);
  const [closing, setClosing] = useState<Closing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const current = templates.find((t) => t.template === tpl);
  const period = current?.period === "year" ? month.slice(0, 4) : month;
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  const loadReports = useCallback(() => { call<Report[]>("/api/v1/reports").then(setReports).catch(() => setReports([])); }, [call]);
  useEffect(() => {
    call<Template[]>("/api/v1/reports/templates").then((t) => { setTemplates(t); setTpl((cur) => (t.find((x) => x.template === cur && x.allowed) ? cur : t.find((x) => x.allowed)?.template ?? cur)); }).catch((e) => setError(e.message));
    loadReports();
  }, [call, loadReports]);
  useEffect(() => { setDoc(null); call<Closing>(`/api/v1/closing?month=${month}`).then(setClosing).catch(() => setClosing(null)); }, [call, month, reports.length]);

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); } catch (e) { setError(e instanceof ApiError ? e.message : "Falha na operação"); } finally { setBusy(false); }
  }

  const body = (format?: string) => ({ template: tpl, period, ...(format ? { format } : {}) });

  async function loadShares(id: string) { call<Share[]>(`/api/v1/reports/${id}/shares`).then((l) => setShares((s) => ({ ...s, [id]: l }))); }

  return (
    <>
      <h1>Relatórios</h1>
      <p className="muted">Relatórios gerenciais com estimativas informadas por você. Os arquivos ficam disponíveis por 30 dias.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      {closing && (
        <div className="card" role="region" aria-label="Fechamento do mês">
          <h2>Fechamento de {closing.month}</h2>
          <p><strong>{closing.done} de {closing.total}</strong> etapas concluídas{closing.ready_to_close ? " — mês pronto para encerrar." : "."}</p>
          <ul style={{ listStyle: "none", padding: 0 }}>
            {closing.items.map((i) => (
              <li key={i.code} style={{ padding: "4px 0" }}>
                <span aria-hidden="true">{i.done ? "✔" : "○"}</span> <strong>{i.done ? "Feito" : "Pendente"}:</strong> {i.label} — <span className="muted">{i.detail}</span>{" "}
                {!i.done && <Link href={i.route}>Resolver</Link>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Gerar relatório</h2>
        <div className="grid2">
          <div>
            <label htmlFor="tpl">Relatório</label>
            <select id="tpl" value={tpl} onChange={(e) => { setTpl(e.target.value); setDoc(null); }}>
              {templates.map((t) => <option key={t.template} value={t.template} disabled={!t.allowed}>{t.name}{t.allowed ? "" : " (sem permissão)"}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="month">{current?.period === "year" ? "Ano (usa o ano do mês escolhido)" : "Competência"}</label>
            <input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} />
          </div>
        </div>
        {current && <p className="muted">{current.description}</p>}
        <p>
          <button type="button" className="secondary" disabled={busy || !current?.allowed} onClick={() => run(async () => setDoc(await call<ReportDoc>("/api/v1/reports/preview", { method: "POST", body: body() })))}>Pré-visualizar</button>{" "}
          {["PDF", "CSV"].map((f) => (
            <button key={f} type="button" disabled={busy || !current?.allowed} onClick={() => run(async () => { await call("/api/v1/reports/generate", { method: "POST", body: body(f) }); setNotice(`Relatório em ${f} gerado.`); loadReports(); })}>Gerar {f}</button>
          ))}
        </p>
      </div>

      {doc && <div className="card" style={{ marginTop: 16 }}><ReportView doc={doc} /></div>}

      <h2>Relatórios gerados</h2>
      {reports.length === 0 ? <p className="muted">Nenhum relatório gerado ainda.</p> : (
        <div className="tablewrap card"><table>
          <thead><tr><th>Relatório</th><th>Período</th><th>Formato</th><th>Expira em</th><th>Ações</th></tr></thead>
          <tbody>{reports.map((r) => (
            <tr key={r.id}>
              <td>{r.template_name}</td><td>{r.period}</td><td>{r.format} · {Math.max(1, Math.round(r.size_bytes / 1024))} KB</td><td>{r.expires_at.slice(0, 10)}</td>
              <td>
                <button className="secondary" onClick={() => run(() => downloadFile(`/api/v1/reports/${r.id}/download`, org.id, r.filename))}>Baixar</button>{" "}
                <button className="secondary" onClick={() => run(async () => { const s = await call<{ id: string; path: string }>(`/api/v1/reports/${r.id}/share`, { method: "POST", body: { days: 7 } }); setLink({ id: r.id, url: publicUrl(s.path) }); loadShares(r.id); })}>Compartilhar por 7 dias</button>{" "}
                <button className="secondary" onClick={() => loadShares(r.id)}>Links</button>{" "}
                <button className="secondary" aria-label={`Excluir ${r.filename}`} onClick={() => window.confirm("Excluir este relatório? Os links compartilhados também deixam de funcionar.") && run(async () => { await call(`/api/v1/reports/${r.id}`, { method: "DELETE" }); loadReports(); })}>Excluir</button>
                {link?.id === r.id && (
                  <p role="status" style={{ marginTop: 8 }}>
                    Link criado (aparece só agora): <input readOnly value={link.url} aria-label="Link de compartilhamento" onFocus={(e) => e.currentTarget.select()} />
                    <span className="muted">Quem tiver o link acessa o arquivo sem login até expirar. Envie só a quem deve ver.</span>
                  </p>
                )}
                {(shares[r.id] ?? []).length > 0 && (
                  <ul style={{ marginTop: 8 }}>{shares[r.id].map((s) => (
                    <li key={s.id}>Expira {s.expires_at.slice(0, 10)} · {s.access_count} acesso(s) · {s.revoked ? "revogado" : "ativo"}{" "}
                      {!s.revoked && <button className="secondary" onClick={() => run(async () => { await call(`/api/v1/report-shares/${s.id}`, { method: "DELETE" }); loadShares(r.id); })}>Revogar</button>}</li>
                  ))}</ul>
                )}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
