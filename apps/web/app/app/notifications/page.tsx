"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useSession } from "../session";

type Kind = { kind: string; title: string; min_interval_days: number };
type Settings = { enabled: boolean; kinds: string[]; quiet_start_hour: number | null; quiet_end_hour: number | null };
type Notif = { id: string; kind: string; title: string; body: string; route: string; read: boolean; created_at: string };
type Alert = { kind: string; severity: string; title: string; detail: string; route: string };

const thisMonth = () => new Date().toISOString().slice(0, 7);

export default function NotificationsPage() {
  const { me, org } = useSession();
  const [kinds, setKinds] = useState<Kind[]>([]);
  const [st, setSt] = useState<Settings | null>(null);
  const [box, setBox] = useState<{ unread_count: number; items: Notif[] } | null>(null);
  const [live, setLive] = useState<Alert[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);
  const load = useCallback(() => {
    call<Kind[]>("/api/v1/notification-kinds").then(setKinds);
    call<Settings>("/api/v1/notification-settings").then(setSt);
    call<{ unread_count: number; items: Notif[] }>("/api/v1/notifications").then(setBox);
    call<{ alerts: Alert[] }>(`/api/v1/alerts?month=${thisMonth()}`).then((r) => setLive(r.alerts)).catch(() => setLive([]));
  }, [call]);
  useEffect(() => { load(); }, [load]);

  // Atualiza a tela na hora e salva o estado mais recente: cliques rápidos não se sobrescrevem.
  async function save(next: Settings) {
    setError(null); setNotice(null);
    setSt(next);
    try { await call<Settings>("/api/v1/notification-settings", { method: "PUT", body: next }); setNotice("Preferências salvas."); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Falha ao salvar"); load(); }
  }

  if (!st) return <p aria-busy="true">Carregando…</p>;
  const toggleKind = (k: string) => save({ ...st, kinds: st.kinds.includes(k) ? st.kinds.filter((x) => x !== k) : [...st.kinds, k] });

  return (
    <>
      <h1>Notificações e alertas</h1>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <div className="card">
        <h2>Preferências</h2>
        <p className="muted">Os avisos são opcionais e ficam desligados até você ligar. Eles não trazem valores financeiros: mostram só um aviso genérico, e os detalhes aparecem aqui, dentro do app.</p>
        <label style={{ fontWeight: 600 }}><input type="checkbox" checked={st.enabled} onChange={(e) => save({ ...st, enabled: e.target.checked })} /> Receber notificações</label>
        <fieldset disabled={!st.enabled} style={{ border: 0, padding: 0 }}>
          <legend className="muted">Quais avisos</legend>
          {kinds.map((k) => (
            <label key={k.kind} style={{ fontWeight: 400 }}><input type="checkbox" checked={st.kinds.includes(k.kind)} onChange={() => toggleKind(k.kind)} /> {k.title} <span className="muted">(no máx. 1 a cada {k.min_interval_days} dia(s))</span></label>
          ))}
          <div className="grid2" style={{ marginTop: 8 }}>
            <div><label htmlFor="qs">Silêncio a partir das (h)</label><input id="qs" type="number" min="0" max="23" value={st.quiet_start_hour ?? ""} onChange={(e) => save({ ...st, quiet_start_hour: e.target.value === "" ? null : Number(e.target.value), quiet_end_hour: st.quiet_end_hour ?? 7 })} /></div>
            <div><label htmlFor="qe">Até as (h)</label><input id="qe" type="number" min="0" max="23" value={st.quiet_end_hour ?? ""} onChange={(e) => save({ ...st, quiet_end_hour: e.target.value === "" ? null : Number(e.target.value), quiet_start_hour: st.quiet_start_hour ?? 22 })} /></div>
          </div>
        </fieldset>
        <p className="muted">No máximo 3 avisos por dia.</p>
        <p><button type="button" className="secondary" onClick={() => call<{ created: unknown[] }>("/api/v1/notifications/generate", { method: "POST", body: {} }).then((r) => { setNotice(`${r.created.length} novo(s) aviso(s).`); load(); })}>Verificar agora</button></p>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Alertas de agora</h2>
        {live === null ? <p aria-busy="true">Calculando…</p> : live.length === 0 ? <p className="muted">Nenhum alerta no mês atual.</p> : (
          <ul>{live.map((a) => <li key={a.kind}><strong>{a.severity === "warning" ? "Atenção:" : "Info:"}</strong> {a.title} — {a.detail} <Link href={a.route}>Abrir</Link></li>)}</ul>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Avisos recebidos {box && box.unread_count > 0 && <span className="muted">({box.unread_count} não lidos)</span>}</h2>
        {box && box.items.length === 0 && <p className="muted">Nenhum aviso ainda.</p>}
        <ul style={{ listStyle: "none", padding: 0 }}>
          {box?.items.map((n) => (
            <li key={n.id} style={{ padding: "6px 0", fontWeight: n.read ? 400 : 700 }}>
              {n.title} <span className="muted">{n.created_at.slice(0, 10)}</span> <Link href={n.route}>Abrir</Link>{" "}
              {!n.read && <button className="secondary" onClick={() => call(`/api/v1/notifications/${n.id}/read`, { method: "POST" }).then(load)}>Marcar como lido</button>}
            </li>
          ))}
        </ul>
        {box && box.unread_count > 0 && <button className="secondary" onClick={() => call("/api/v1/notifications/read-all", { method: "POST" }).then(load)}>Marcar todos como lidos</button>}
      </div>
    </>
  );
}
