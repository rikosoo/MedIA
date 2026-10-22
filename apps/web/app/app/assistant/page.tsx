"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { parseBRLToCents } from "@/lib/money";
import { useSession } from "../session";

type Fact = { key: string; label: string; shown: string; source: string };
type Answer = {
  intent: string; month: string; summary: string; data_used: string[]; scenarios: string[]; constraints: string[];
  risks: string[]; actions: { label: string; route: string }[]; simulation_link: string | null; facts: Fact[]; assumptions: string[];
};
type Msg = { id: string; role: "USER" | "ASSISTANT"; content: string; answer: Answer | null; saved: boolean };
type Conv = { id: string; summary: string };

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <section>
      <h4>{title}</h4>
      <ul>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </section>
  );
}

function plain(a: Answer) {
  const sec = (t: string, xs: string[]) => (xs.length ? `\n${t}:\n${xs.map((x) => `- ${x}`).join("\n")}` : "");
  return `${a.summary}${sec("Dados utilizados", a.data_used)}${sec("Cenários", a.scenarios)}${sec("Restrições", a.constraints)}${sec("Riscos e incertezas", a.risks)}${sec("Ações sugeridas", a.actions.map((x) => x.label))}`;
}

export default function AssistantPage() {
  const { me, org } = useSession();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [convId, setConvId] = useState<string | null>(null);
  const [convs, setConvs] = useState<Conv[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastQ, setLastQ] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  const loadConvs = useCallback(() => { call<Conv[]>("/api/v1/assistant/conversations").then(setConvs).catch(() => setConvs([])); }, [call]);
  useEffect(() => {
    call<string[]>("/api/v1/assistant/suggestions").then(setSuggestions).catch((e) => setError(e instanceof ApiError ? e.message : "Falha ao carregar"));
    loadConvs();
  }, [call, loadConvs]);

  async function send(text: string, target?: number) {
    const q = text.trim();
    if (q.length < 2) return;
    setBusy(true); setError(null); setNotice(null); setLastQ(q);
    setMsgs((m) => [...m, { id: `u${Date.now()}`, role: "USER", content: q, answer: null, saved: false }]);
    try {
      const r = await call<{ conversation_id: string; message: Msg & { content: string } }>("/api/v1/assistant/chat", {
        method: "POST", body: { message: q, conversation_id: convId, target_cents: target ?? null },
      });
      setConvId(r.conversation_id);
      setMsgs((m) => [...m, r.message]);
      loadConvs();
      if (input.current) input.current.value = "";
    } catch (err) { setError(err instanceof ApiError ? err.message : "Falha ao perguntar"); } finally { setBusy(false); }
  }

  async function open(id: string) {
    setError(null);
    try {
      const c = await call<{ id: string; messages: Msg[] }>(`/api/v1/assistant/conversations/${id}`);
      setConvId(c.id); setMsgs(c.messages);
    } catch { setError("Não foi possível abrir a conversa."); }
  }

  async function remove(id: string) {
    await call(`/api/v1/assistant/conversations/${id}`, { method: "DELETE" });
    if (convId === id) { setConvId(null); setMsgs([]); }
    loadConvs();
  }

  async function toggleSave(m: Msg) {
    try {
      const r = await call<Msg>(`/api/v1/assistant/messages/${m.id}/save`, { method: "POST", body: { saved: !m.saved } });
      setMsgs((xs) => xs.map((x) => (x.id === m.id ? { ...x, saved: r.saved } : x)));
      setNotice(r.saved ? "Recomendação salva (não altera seu planejamento)." : "Recomendação removida dos salvos.");
    } catch { setError("Não foi possível salvar."); }
  }

  async function copy(a: Answer) {
    try { await navigator.clipboard.writeText(plain(a)); setNotice("Resposta copiada."); } catch { setError("Não foi possível copiar."); }
  }

  function redo(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const raw = String(new FormData(e.currentTarget).get("target") || "");
    const c = parseBRLToCents(raw);
    if (c === null || c <= 0) return setError("Meta inválida. Use o formato 50.000,00");
    void send(lastQ || "Onde mexo para bater a meta?", c);
  }

  return (
    <div>
      <h1>Consultor IA</h1>
      <p className="muted">Explica os números que o app já calculou e sugere ações. Não inventa valores e <strong>não altera nenhum dado</strong>. Tributos são estimativas.</p>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <div className="card">
        <label htmlFor="q">Sua pergunta</label>
        <form onSubmit={(e) => { e.preventDefault(); void send(String(new FormData(e.currentTarget).get("q") || "")); }}>
          <textarea id="q" name="q" ref={input} rows={2} maxLength={500} placeholder="Ex.: Esse mês preciso ganhar R$ 50 mil; onde mexo?" />
          <p className="muted"><small>Não escreva CPF, telefone ou e-mail de pacientes: eles são removidos do histórico, mas evite.</small></p>
          <button type="submit" disabled={busy}>{busy ? "Calculando…" : "Perguntar"}</button>
        </form>
        <p><strong>Perguntas sugeridas</strong></p>
        <ul>{suggestions.map((s) => <li key={s}><button type="button" className="secondary" onClick={() => void send(s)} disabled={busy}>{s}</button></li>)}</ul>
      </div>

      {msgs.map((m) =>
        m.role === "USER" ? (
          <p key={m.id}><strong>Você:</strong> {m.content}</p>
        ) : m.answer ? (
          <article key={m.id} className="card" aria-label="Resposta do consultor">
            <h3>Resumo</h3>
            <p>{m.answer.summary}</p>
            <List title="Dados utilizados" items={m.answer.data_used} />
            <List title="Cenários" items={m.answer.scenarios} />
            <List title="Restrições" items={m.answer.constraints} />
            <List title="Riscos e incertezas" items={[...m.answer.risks, ...m.answer.assumptions]} />
            {m.answer.actions.length > 0 && (
              <section>
                <h4>Ações sugeridas</h4>
                <ul>{m.answer.actions.map((x) => <li key={x.route + x.label}><Link href={x.route}>{x.label}</Link></li>)}</ul>
              </section>
            )}
            {m.answer.simulation_link && <p><Link href={m.answer.simulation_link}>Abrir cenário no Otimizador</Link></p>}
            {m.answer.facts.length > 0 && (
              <details>
                <summary>Ver cálculo</summary>
                <div className="tablewrap">
                  <table>
                    <thead><tr><th>Dado</th><th>Valor</th><th>Origem</th></tr></thead>
                    <tbody>{m.answer.facts.map((f) => <tr key={f.key}><td>{f.label}</td><td>{f.shown}</td><td>{f.source}</td></tr>)}</tbody>
                  </table>
                </div>
              </details>
            )}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
              <button type="button" className="secondary" onClick={() => void toggleSave(m)}>{m.saved ? "Remover dos salvos" : "Salvar recomendação"}</button>
              <button type="button" className="secondary" onClick={() => void copy(m.answer!)}>Copiar</button>
            </div>
            {["GAP"].includes(m.answer.intent) && (
              <form onSubmit={redo} style={{ marginTop: 12 }}>
                <label htmlFor={`t-${m.id}`}>Refazer com outra meta (R$)</label>
                <input id={`t-${m.id}`} name="target" inputMode="decimal" placeholder="40.000,00" />
                <button type="submit" className="secondary" disabled={busy}>Refazer</button>
              </form>
            )}
          </article>
        ) : null,
      )}

      {convs.length > 0 && (
        <div className="card">
          <h3>Conversas anteriores</h3>
          <ul>
            {convs.map((c) => (
              <li key={c.id}>
                <button type="button" className="secondary" onClick={() => void open(c.id)}>{c.summary}</button>{" "}
                <button type="button" className="secondary" aria-label={`Apagar conversa: ${c.summary}`} onClick={() => void remove(c.id)}>Apagar</button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
