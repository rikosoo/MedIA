"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatBRL } from "@/lib/money";
import { useSession } from "@/app/app/session";

type Row = {
  line: number; state: string; message: string; warnings: string[]; type: string | null;
  competence_month: string | null; paid_at: string | null; amount_cents: number | null;
  is_reversal: boolean | null; description: string | null;
};
type Result = {
  dry_run: boolean; counts: { new: number; reconcile: number; duplicates: number; errors: number };
  rows: Row[]; errors: { line: number; message: string }[]; truncated_preview: boolean;
  already_imported: { filename: string; created_at: string } | null;
  job: { id: string } | null;
};
type Job = { id: string; filename: string; status: string; imported_count: number; reconciled_count: number; duplicate_count: number; error_count: number; created_at: string };

const STATE: Record<string, string> = {
  NEW: "Novo", RECONCILE: "Concilia planejado", DUPLICATE_EXISTING: "Já existe", DUPLICATE_IN_FILE: "Repetido no arquivo", ERROR: "Erro",
};

export function ImportPanel({ month, onChanged }: { month: string; onChanged: () => void }) {
  const { me, org } = useSession();
  const [content, setContent] = useState<string | null>(null);
  const [filename, setFilename] = useState("");
  const [preview, setPreview] = useState<Result | null>(null);
  const [skipInvalid, setSkipInvalid] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);
  const loadJobs = useCallback(() => { call<Job[]>("/api/v1/imports").then(setJobs).catch(() => setJobs([])); }, [call]);
  useEffect(loadJobs, [loadJobs]);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    setPreview(null); setError(null); setNotice(null);
    if (!f) return;
    if (f.size > 1_900_000) return setError("Arquivo grande demais (máximo ~2 MB).");
    const text = await f.text();
    setContent(text); setFilename(f.name);
    setBusy(true);
    try { setPreview(await call<Result>("/api/v1/imports", { method: "POST", body: { filename: f.name, content: text, dry_run: true } })); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Falha ao ler o arquivo"); }
    finally { setBusy(false); }
  }

  async function confirm(reimport = false) {
    if (!content) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await call<Result>("/api/v1/imports", { method: "POST", body: { filename, content, dry_run: false, skip_invalid: skipInvalid, allow_reimport: reimport } });
      setNotice(`Importação concluída: ${r.counts.new} novos, ${r.counts.reconcile} conciliados, ${r.counts.duplicates} duplicados ignorados.`);
      setPreview(null); setContent(null);
      loadJobs(); onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao importar");
    } finally { setBusy(false); }
  }

  async function undo(id: string) {
    if (!window.confirm("Desfazer esta importação? Os lançamentos criados por ela serão excluídos.")) return;
    setBusy(true); setError(null);
    try { await call(`/api/v1/imports/${id}/undo`, { method: "POST" }); setNotice("Importação desfeita."); loadJobs(); onChanged(); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Falha ao desfazer"); }
    finally { setBusy(false); }
  }

  async function download() {
    setError(null);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/v1/export/transactions.csv?month=${month}`, { credentials: "include", headers: { "X-Organization-Id": org.id } });
      if (!res.ok) throw new Error(String(res.status));
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url; a.download = `lancamentos-${month}.csv`; a.click();
      URL.revokeObjectURL(url);
    } catch { setError("Não foi possível exportar."); }
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2>Importar e exportar CSV</h2>
      <p className="muted">Aceita extrato bancário (data, descrição, valor; débito negativo) ou colunas como competência, tipo, categoria, situação. Nada é gravado antes da pré-visualização; arquivos repetidos e linhas já lançadas não duplicam receita.</p>
      <label htmlFor="csv">Arquivo CSV</label>
      <input id="csv" type="file" accept=".csv,text/csv" onChange={onFile} />
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      {preview && (
        <div style={{ marginTop: 12 }} role="region" aria-label="Pré-visualização da importação">
          <h3>Pré-visualização de {filename}</h3>
          <p>
            <strong>{preview.counts.new}</strong> novos · <strong>{preview.counts.reconcile}</strong> conciliam com lançamentos planejados ·{" "}
            <strong>{preview.counts.duplicates}</strong> duplicados (serão ignorados) · <strong>{preview.counts.errors}</strong> com erro
          </p>
          {preview.already_imported && <p className="error">Atenção: um arquivo idêntico já foi importado em {preview.already_imported.created_at.slice(0, 10)}.</p>}
          {preview.errors.length > 0 && <ul>{preview.errors.slice(0, 10).map((e) => <li key={e.line} className="error">Linha {e.line}: {e.message}</li>)}</ul>}
          <div className="tablewrap"><table>
            <thead><tr><th>Linha</th><th>Situação</th><th>Descrição</th><th>Valor</th></tr></thead>
            <tbody>{preview.rows.slice(0, 50).map((r) => (
              <tr key={r.line}>
                <td>{r.line}</td><td>{STATE[r.state]}{r.message ? <><br /><span className="muted">{r.message}</span></> : null}</td>
                <td>{r.description ?? "—"}{r.warnings.map((w) => <><br key={w} /><span className="muted">{w}</span></>)}</td>
                <td>{r.amount_cents === null ? "—" : `${r.type === "EXPENSE" ? "−" : "+"}${formatBRL(r.amount_cents)}`}</td>
              </tr>))}</tbody>
          </table></div>
          {preview.rows.length > 50 && <p className="muted">Mostrando 50 de {preview.rows.length} linhas.</p>}
          {preview.counts.errors > 0 && <label style={{ fontWeight: 400 }}><input type="checkbox" checked={skipInvalid} onChange={(e) => setSkipInvalid(e.target.checked)} /> Importar só as linhas válidas</label>}
          <p>
            <button type="button" disabled={busy || (preview.counts.errors > 0 && !skipInvalid) || (preview.counts.new + preview.counts.reconcile === 0)} onClick={() => confirm(false)}>Confirmar importação</button>{" "}
            <button type="button" className="secondary" onClick={() => { setPreview(null); setContent(null); }}>Cancelar</button>
          </p>
        </div>
      )}

      <p><button type="button" className="secondary" onClick={download}>Exportar {month} em CSV</button></p>

      {jobs.length > 0 && (
        <>
          <h3>Importações anteriores</h3>
          <div className="tablewrap"><table>
            <thead><tr><th>Arquivo</th><th>Data</th><th>Resultado</th><th /></tr></thead>
            <tbody>{jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.filename}</td><td>{j.created_at.slice(0, 10)}</td>
                <td>{j.status === "UNDONE" ? "Desfeita" : `${j.imported_count} novos · ${j.reconciled_count} conciliados · ${j.duplicate_count} duplicados`}</td>
                <td>{j.status === "COMPLETED" && <button className="secondary" disabled={busy} onClick={() => undo(j.id)} aria-label={`Desfazer importação de ${j.filename}`}>Desfazer</button>}</td>
              </tr>))}</tbody>
          </table></div>
        </>
      )}
    </div>
  );
}
