"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useSession } from "../session";

type OrgData = { name: string; location_uf: string | null; location_city: string | null; timezone: string };
type Sess = { id: string; created_at: string; expires_at: string; current: boolean };

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const when = (iso: string) => new Date(iso).toLocaleString("pt-BR");

export default function SettingsPage() {
  const { me, org } = useSession();
  const router = useRouter();
  const isOwner = org.role === "OWNER";
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [orgData, setOrgData] = useState<OrgData | null>(null);
  const [sessions, setSessions] = useState<Sess[]>([]);
  const [mfa, setMfa] = useState<boolean>(!!me.mfa_enabled);
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const call = useCallback(<T,>(path: string, o: { method?: string; body?: unknown } = {}) => api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }), [me.csrf_token, org.id]);

  const loadSessions = useCallback(() => { call<Sess[]>("/api/v1/auth/sessions").then(setSessions).catch(() => setSessions([])); }, [call]);
  useEffect(() => {
    loadSessions();
    call<OrgData>("/api/v1/organizations/current").then(setOrgData).catch(() => setOrgData(null));
  }, [call, loadSessions]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ kind: "ok", text: ok }); } catch (e) { setMsg({ kind: "err", text: e instanceof ApiError ? e.message : "Falha na operação" }); } finally { setBusy(false); }
  }
  const val = (e: React.FormEvent<HTMLFormElement>, k: string) => String(new FormData(e.currentTarget).get(k) ?? "");

  async function download(path: string, name: string) {
    setMsg(null);
    try {
      const res = await fetch(`${BASE}${path}`, { credentials: "include", headers: { "X-Organization-Id": org.id } });
      if (!res.ok) throw new Error();
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
      setMsg({ kind: "ok", text: "Exportação gerada." });
    } catch { setMsg({ kind: "err", text: "Não foi possível exportar." }); }
  }

  return (
    <div>
      <h1>Configurações</h1>
      {msg && <p role={msg.kind === "err" ? "alert" : "status"} className={msg.kind === "err" ? "error" : undefined}>{msg.text}</p>}

      <section className="card" aria-labelledby="s-profile">
        <h2 id="s-profile">Perfil</h2>
        <form onSubmit={(e) => { e.preventDefault(); void act(() => call("/api/v1/me", { method: "PATCH", body: { name: val(e, "name") } }), "Perfil salvo."); }}>
          <label htmlFor="pname">Nome</label>
          <input id="pname" name="name" defaultValue={me.name} required maxLength={200} />
          <p className="muted">E-mail: {me.email}</p>
          <button type="submit" disabled={busy}>Salvar</button>
        </form>
      </section>

      {isOwner && orgData && (
        <section className="card" aria-labelledby="s-org">
          <h2 id="s-org">Organização</h2>
          <form onSubmit={(e) => { e.preventDefault(); void act(() => call("/api/v1/organizations/current", { method: "PATCH", body: { name: val(e, "oname"), location_uf: val(e, "uf").toUpperCase() || null, location_city: val(e, "city") || null } }), "Organização salva."); }}>
            <label htmlFor="oname">Nome da clínica ou do uso pessoal</label>
            <input id="oname" name="oname" defaultValue={orgData.name} required maxLength={200} />
            <div className="grid2">
              <div><label htmlFor="uf">UF (feriados e região)</label><input id="uf" name="uf" defaultValue={orgData.location_uf ?? ""} maxLength={2} /></div>
              <div><label htmlFor="city">Cidade</label><input id="city" name="city" defaultValue={orgData.location_city ?? ""} maxLength={120} /></div>
            </div>
            <p className="muted">Fuso horário: {orgData.timezone}</p>
            <button type="submit" disabled={busy}>Salvar organização</button>
          </form>
        </section>
      )}

      <section className="card" aria-labelledby="s-notif">
        <h2 id="s-notif">Notificações</h2>
        <p>Avisos são opcionais e vêm desligados. <Link href="/app/notifications">Configurar notificações</Link></p>
      </section>

      <section className="card" aria-labelledby="s-sec">
        <h2 id="s-sec">Segurança</h2>
        <h3>Trocar senha</h3>
        <form onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; void act(async () => { await call("/api/v1/auth/change-password", { method: "POST", body: { current_password: val(e, "cur"), new_password: val(e, "new") } }); f.reset(); loadSessions(); }, "Senha alterada. Os outros dispositivos foram desconectados."); }}>
          <label htmlFor="cur">Senha atual</label>
          <input id="cur" name="cur" type="password" required autoComplete="current-password" />
          <label htmlFor="new">Nova senha (mínimo 10 caracteres)</label>
          <input id="new" name="new" type="password" required minLength={10} autoComplete="new-password" />
          <button type="submit" disabled={busy}>Trocar senha</button>
        </form>

        <h3>Verificação em duas etapas (2FA)</h3>
        {mfa ? (
          <form onSubmit={(e) => { e.preventDefault(); void act(async () => { await call("/api/v1/auth/mfa/disable", { method: "POST", body: { password: val(e, "mpwd"), code: val(e, "mcode") } }); setMfa(false); }, "2FA desativado."); }}>
            <p>O 2FA está <strong>ativo</strong>.</p>
            <label htmlFor="mpwd">Senha</label><input id="mpwd" name="mpwd" type="password" required />
            <label htmlFor="mcode">Código do app (ou de recuperação)</label><input id="mcode" name="mcode" inputMode="numeric" required />
            <button type="submit" className="secondary" disabled={busy}>Desativar 2FA</button>
          </form>
        ) : setup ? (
          <form onSubmit={(e) => { e.preventDefault(); void act(async () => { const r = await call<{ recovery_codes: string[] }>("/api/v1/auth/mfa/enable", { method: "POST", body: { code: val(e, "ecode") } }); setCodes(r.recovery_codes); setMfa(true); setSetup(null); }, "2FA ativado."); }}>
            <p>No app autenticador (Google Authenticator, Authy, 1Password…), adicione uma conta manual com esta chave:</p>
            <p><code aria-label="Chave do autenticador">{setup.secret}</code></p>
            <label htmlFor="ecode">Código de 6 dígitos gerado pelo app</label>
            <input id="ecode" name="ecode" inputMode="numeric" required autoComplete="one-time-code" />
            <button type="submit" disabled={busy}>Ativar 2FA</button>
          </form>
        ) : (
          <p><button type="button" disabled={busy} onClick={() => void act(async () => setSetup(await call("/api/v1/auth/mfa/setup", { method: "POST" })), "Chave gerada: confirme com um código.")}>Ativar 2FA</button></p>
        )}
        {codes && (
          <div role="alert" className="card">
            <strong>Guarde estes códigos de recuperação.</strong> Cada um vale uma vez e não serão mostrados de novo.
            <ul>{codes.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
            <button type="button" className="secondary" onClick={() => setCodes(null)}>Já guardei</button>
          </div>
        )}

        <h3>Dispositivos conectados</h3>
        <ul>
          {sessions.map((s) => (
            <li key={s.id}>
              Iniciada em {when(s.created_at)} {s.current ? <strong>(este dispositivo)</strong> : (
                <button type="button" className="secondary" disabled={busy} onClick={() => void act(async () => { await call(`/api/v1/auth/sessions/${s.id}`, { method: "DELETE" }); loadSessions(); }, "Sessão encerrada.")}>Encerrar</button>
              )}
            </li>
          ))}
        </ul>
        {sessions.length > 1 && <button type="button" className="secondary" disabled={busy} onClick={() => void act(async () => { await call("/api/v1/auth/sessions/revoke-others", { method: "POST" }); loadSessions(); }, "Outras sessões encerradas.")}>Encerrar todas as outras</button>}
      </section>

      <section className="card" aria-labelledby="s-priv">
        <h2 id="s-priv">Privacidade e dados (LGPD)</h2>
        <p>Você aceitou os <Link href="/termos">Termos de Uso</Link> e a <Link href="/privacidade">Política de Privacidade</Link> ao criar a conta.</p>
        <p>
          <button type="button" className="secondary" onClick={() => void download("/api/v1/me/export", "meus-dados.json")}>Baixar meus dados pessoais</button>{" "}
          {isOwner && <button type="button" className="secondary" onClick={() => void download("/api/v1/billing/export", "financemed-dados.json")}>Baixar dados da organização</button>}
        </p>
        <h3>Excluir conta</h3>
        <p>Remove seus dados e, se você for o único proprietário, os da organização. Faturas e registros de auditoria são retidos sem dados pessoais. <strong>Não dá para desfazer.</strong> Considere exportar antes.</p>
        <form onSubmit={(e) => { e.preventDefault(); void act(async () => { await call("/api/v1/me", { method: "DELETE", body: { password: val(e, "dpwd"), email: val(e, "demail"), otp: val(e, "dotp") || null } }); router.replace("/login"); }, "Conta excluída."); }}>
          <label htmlFor="dpwd">Senha</label><input id="dpwd" name="dpwd" type="password" required autoComplete="current-password" />
          <label htmlFor="demail">Digite seu e-mail ({me.email}) para confirmar</label><input id="demail" name="demail" type="email" required autoComplete="off" />
          {mfa && (<><label htmlFor="dotp">Código do 2FA</label><input id="dotp" name="dotp" inputMode="numeric" required /></>)}
          <button type="submit" disabled={busy}>Excluir minha conta</button>
        </form>
      </section>
    </div>
  );
}
