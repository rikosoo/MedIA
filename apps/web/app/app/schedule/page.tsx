"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatMinutes, toOffsetISO } from "@/lib/time";
import { useSession } from "../session";

const TZ = "America/Sao_Paulo"; // fuso padrão da organização (PRD §5.1)

type Person = { user_id: string; name: string; role: string };
type Service = { id: string; name: string };
type Day = { date: string; closed_reason: string | null; available_minutes: number; remaining_minutes: number; slots_remaining: number };
type Capacity = {
  availability_minutes: number; closed_minutes: number; blocked_minutes: number; booked_consult_minutes: number;
  remaining_minutes: number; conditional_on_call_minutes: number; slot_minutes: number; slots_remaining: number;
  days: Day[]; warnings: string[];
  service?: { expected_minutes_per_new_patient: string; new_patients_remaining: number };
};
type Overview = {
  active_minutes: number; committed_minutes: number; night_shifts: number; feasible: boolean;
  limits: { max_active_minutes: number | null; max_committed_minutes: number | null; max_night_shifts: number | null } | null;
  over_limit: Record<string, number>;
  conflicts: { a_id: string; b_id: string; kind: string; overlap_minutes: number }[];
  rest_violations: { after_id: string; next_id: string; required_minutes: number; gap_minutes: number }[];
  events_on_closed_days: { event_id: string; dates: string[]; reason: string }[];
};
type Ev = { id: string; type: string; title: string; starts_at: string; ends_at: string; status: string };
type Holiday = { id: string; date: string; name: string; scope: string; confirmed: boolean };
type Leave = { id: string; staff_role: string; start_date: string; end_date: string };

const TYPES: Record<string, string> = {
  CONSULT_NEW: "Consulta inicial", CONSULT_RETURN: "Retorno", SHIFT: "Plantão", ON_CALL: "Sobreaviso",
  EMPLOYMENT: "CLT", TELEMEDICINE: "Telemedicina", PROCEDURE: "Procedimento", TRAVEL: "Deslocamento",
  BREAK: "Pausa", BLOCK: "Bloqueio",
};
const WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
const hm = (iso: string) => iso.slice(8, 10) + "/" + iso.slice(5, 7) + " " + iso.slice(11, 16);
const thisMonth = () => new Date().toISOString().slice(0, 7);
const lastDay = (month: string) => {
  const [y, m] = month.split("-").map(Number);
  return `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
};

export default function SchedulePage() {
  const { me, org } = useSession();
  const [month, setMonth] = useState(thisMonth());
  const [people, setPeople] = useState<Person[]>([]);
  const [uid, setUid] = useState<string>("");
  const [services, setServices] = useState<Service[]>([]);
  const [serviceId, setServiceId] = useState("");
  const [cap, setCap] = useState<Capacity | null>(null);
  const [ov, setOv] = useState<Overview | null>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const [holidays, setHolidays] = useState<Holiday[]>([]);
  const [leaves, setLeaves] = useState<Leave[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, unknown> | null>(null);
  const [conflictMsg, setConflictMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const year = Number(month.slice(0, 4));

  const call = useCallback(
    <T,>(path: string, o: { method?: string; body?: unknown } = {}) =>
      api<T>(path, { ...o, csrf: me.csrf_token, orgId: org.id }),
    [me.csrf_token, org.id],
  );

  useEffect(() => {
    call<Person[]>("/api/v1/schedule/people").then((p) => { setPeople(p); setUid((u) => u || p[0]?.user_id || ""); }).catch((e) => setError(e.message));
    call<Service[]>("/api/v1/clinic-services").then(setServices).catch(() => setServices([])); // sem permissão: tudo bem
  }, [call]);

  const load = useCallback(async () => {
    if (!uid) return;
    try {
      const svc = serviceId ? `&service_id=${serviceId}` : "";
      const [c, o, e, h, l] = await Promise.all([
        call<Capacity>(`/api/v1/schedule/capacity?month=${month}&user_id=${uid}${svc}`),
        call<Overview>(`/api/v1/schedule/overview?month=${month}&user_id=${uid}`),
        call<Ev[]>(`/api/v1/schedule/events?from=${month}-01&to=${lastDay(month)}&user_id=${uid}`),
        call<Holiday[]>(`/api/v1/holidays?year=${year}`),
        call<Leave[]>(`/api/v1/leaves?year=${year}`),
      ]);
      setCap(c); setOv(o); setEvents(e); setHolidays(h); setLeaves(l);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha ao carregar a agenda");
    }
  }, [call, month, uid, serviceId, year]);
  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); if (ok) setNotice(ok); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Falha na operação"); } finally { setBusy(false); }
  }

  async function postEvent(body: Record<string, unknown>) {
    setBusy(true); setError(null); setConflictMsg(null);
    try {
      await call("/api/v1/schedule/events", { method: "POST", body });
      setPending(null);
      await load();
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { setPending(body); setConflictMsg(e.message); }
      else setError(e instanceof ApiError ? e.message : "Falha ao salvar");
      return false;
    } finally { setBusy(false); }
  }

  function onWeekly(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const weekdays = WEEKDAYS.map((_, i) => i).filter((i) => f.get(`wd${i}`) === "on");
    if (weekdays.length === 0) return setError("Escolha ao menos um dia da semana.");
    run(async () => {
      const r = await call<{ created: number; skipped_existing: number }>("/api/v1/availability/weekly", {
        method: "POST",
        body: { weekdays, start_time: f.get("start"), end_time: f.get("end"), from_date: `${month}-01`, to_date: lastDay(month), user_id: uid },
      });
      setNotice(`${r.created} janelas criadas, ${r.skipped_existing} já existiam.`);
    });
  }

  async function onEvent(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(e.currentTarget);
    let body: Record<string, unknown>;
    try {
      body = {
        type: f.get("type"), title: f.get("title"), user_id: uid,
        starts_at: toOffsetISO(String(f.get("starts")), TZ), ends_at: toOffsetISO(String(f.get("ends")), TZ),
        allow_overlap: f.get("overlap") === "on",
      };
    } catch { return setError("Informe data e hora de início e fim."); }
    if (await postEvent(body)) form.reset();
  }

  function onLimits(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const h = (k: string) => (f.get(k) ? Math.round(Number(String(f.get(k)).replace(",", ".")) * 60) : null);
    run(() => call("/api/v1/time-constraints", {
      method: "PUT",
      body: {
        month, user_id: uid, max_active_minutes: h("active"), max_committed_minutes: h("committed"),
        min_rest_minutes: h("rest") ?? 0, max_night_shifts: f.get("night") ? Number(f.get("night")) : null,
        days_off: String(f.get("off") || "").split(/[\s,;]+/).filter(Boolean),
      },
    }), "Limites salvos para este mês.");
  }

  function onLeave(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    run(() => call("/api/v1/leaves", {
      method: "POST",
      body: { staff_role: f.get("role"), start_date: f.get("from"), end_date: f.get("to"), user_id: f.get("role") === "PHYSICIAN" ? uid : null },
    }));
  }

  const alerts: string[] = [];
  ov?.conflicts.forEach((c) => alerts.push(`${c.kind === "HARD" ? "Conflito de horário" : "Sobreposição condicional (sobreaviso)"}: ${formatMinutes(c.overlap_minutes)} de sobreposição.`));
  ov?.rest_violations.forEach((r) => alerts.push(`Descanso insuficiente: faltam ${formatMinutes(r.required_minutes - r.gap_minutes)} (exigido ${formatMinutes(r.required_minutes)}).`));
  ov?.events_on_closed_days.forEach((d) => alerts.push(`Atividade em dia fechado (${d.reason}): ${d.dates.join(", ")}.`));
  if (ov?.over_limit.active_excess_minutes) alerts.push(`Acima do limite de horas ativas por ${formatMinutes(ov.over_limit.active_excess_minutes)}.`);
  if (ov?.over_limit.committed_excess_minutes) alerts.push(`Acima do limite de horas comprometidas por ${formatMinutes(ov.over_limit.committed_excess_minutes)}.`);
  if (ov?.over_limit.night_shifts_excess) alerts.push(`Acima do limite de plantões noturnos em ${ov.over_limit.night_shifts_excess}.`);

  return (
    <>
      <h1>Agenda &amp; Capacidade</h1>
      <div className="grid2">
        <div><label htmlFor="month">Mês</label><input id="month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} /></div>
        <div>
          <label htmlFor="person">Agenda de</label>
          <select id="person" value={uid} onChange={(e) => setUid(e.target.value)}>
            {people.map((p) => <option key={p.user_id} value={p.user_id}>{p.name}</option>)}
          </select>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      {cap && ov && (
        <div className="grid2" style={{ marginTop: 16 }}>
          <div className="card">
            <h2>Capacidade real</h2>
            <p>Disponível: <strong>{formatMinutes(cap.availability_minutes)}</strong></p>
            <p className="muted">− fechado (feriados, férias, folgas): {formatMinutes(cap.closed_minutes)}<br />− compromissos e descansos: {formatMinutes(cap.blocked_minutes)}<br />− consultas já marcadas: {formatMinutes(cap.booked_consult_minutes)}</p>
            <p>Livre: <strong>{formatMinutes(cap.remaining_minutes)}</strong> · {cap.slots_remaining} horários de {cap.slot_minutes} min</p>
            {cap.conditional_on_call_minutes > 0 && <p className="muted">{formatMinutes(cap.conditional_on_call_minutes)} do livre coincidem com sobreaviso permitido (condicional ao acionamento).</p>}
            {cap.warnings.map((w) => <p key={w} className="error">{w}</p>)}
            {services.length > 0 && (
              <>
                <label htmlFor="svc">Pacientes novos para o serviço</label>
                <select id="svc" value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
                  <option value="">—</option>
                  {services.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                {cap.service && <p>Cabem <strong>{cap.service.new_patients_remaining}</strong> pacientes novos ({cap.service.expected_minutes_per_new_patient.replace(".", ",")} min cada, com retornos).</p>}
              </>
            )}
          </div>
          <div className="card">
            <h2>Jornada do mês</h2>
            <p>Horas ativas: <strong>{formatMinutes(ov.active_minutes)}</strong>{ov.limits?.max_active_minutes != null && <> de {formatMinutes(ov.limits.max_active_minutes)}</>}</p>
            <p>Horas comprometidas: <strong>{formatMinutes(ov.committed_minutes)}</strong>{ov.limits?.max_committed_minutes != null && <> de {formatMinutes(ov.limits.max_committed_minutes)}</>}</p>
            <p>Plantões noturnos: <strong>{ov.night_shifts}</strong>{ov.limits?.max_night_shifts != null && <> de {ov.limits.max_night_shifts}</>}</p>
            <p><strong>{ov.feasible ? "Dentro dos limites" : "Atenção: há problemas na agenda"}</strong></p>
            {alerts.length > 0 && <ul>{alerts.map((a, i) => <li key={i} className="error">{a}</li>)}</ul>}
            <p className="muted">Comprometido = ativo + sobreaviso + deslocamento, sem contar a sobreposição duas vezes.</p>
          </div>
        </div>
      )}

      <form className="card" onSubmit={onWeekly} style={{ marginTop: 16 }}>
        <h2>Turno semanal de atendimento</h2>
        <fieldset style={{ border: 0, padding: 0 }}>
          <legend>Dias da semana</legend>
          {WEEKDAYS.map((d, i) => <label key={d} style={{ display: "inline-block", marginRight: 12, fontWeight: 400 }}><input type="checkbox" name={`wd${i}`} style={{ width: "auto", minHeight: 0 }} /> {d}</label>)}
        </fieldset>
        <div className="grid2">
          <div><label htmlFor="ws">Início</label><input id="ws" name="start" type="time" required defaultValue="08:00" /></div>
          <div><label htmlFor="we">Fim</label><input id="we" name="end" type="time" required defaultValue="12:00" /></div>
        </div>
        <p className="muted">Vale para todo o mês selecionado; janelas já cadastradas são ignoradas.</p>
        <p><button type="submit" disabled={busy}>Adicionar turno</button></p>
      </form>

      <form className="card" onSubmit={onEvent} style={{ marginTop: 16 }}>
        <h2>Novo evento</h2>
        <div className="grid2">
          <div><label htmlFor="etype">Tipo</label><select id="etype" name="type">{Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
          <div><label htmlFor="etitle">Título (opcional)</label><input id="etitle" name="title" maxLength={200} /></div>
          <div><label htmlFor="estart">Início</label><input id="estart" name="starts" type="datetime-local" required /></div>
          <div><label htmlFor="eend">Fim</label><input id="eend" name="ends" type="datetime-local" required /></div>
        </div>
        <label style={{ fontWeight: 400 }}><input type="checkbox" name="overlap" style={{ width: "auto", minHeight: 0 }} /> Sobreaviso pode coincidir com outra atividade (condicional ao acionamento)</label>
        {conflictMsg && pending && (
          <p className="error" role="alert">{conflictMsg}. Há sobreposição com outro compromisso.{" "}
            <button type="button" className="secondary" onClick={() => postEvent({ ...pending, allow_conflict: true })}>Salvar mesmo assim</button></p>
        )}
        <p><button type="submit" disabled={busy}>Salvar evento</button></p>
        <p className="muted">Horários no fuso {TZ}.</p>
      </form>

      <h2>Eventos do mês</h2>
      {events.length === 0 ? <p className="muted">Nenhum evento neste mês.</p> : (
        <div className="tablewrap card"><table>
          <thead><tr><th>Quando</th><th>Tipo</th><th>Situação</th><th /></tr></thead>
          <tbody>{events.map((e) => (
            <tr key={e.id}>
              <td>{hm(e.starts_at)} → {hm(e.ends_at)}</td><td>{TYPES[e.type]}{e.title ? ` · ${e.title}` : ""}</td><td>{e.status === "DONE" ? "Realizado" : "Planejado"}</td>
              <td>
                {e.status === "PLANNED" && <><button className="secondary" onClick={() => run(() => call(`/api/v1/schedule/events/${e.id}`, { method: "PATCH", body: { status: "DONE" } }))}>Marcar realizado</button>{" "}</>}
                <button className="secondary" aria-label={`Excluir evento de ${hm(e.starts_at)}`} onClick={() => confirm("Excluir este evento?") && run(() => call(`/api/v1/schedule/events/${e.id}`, { method: "DELETE" }))}>Excluir</button>
              </td>
            </tr>))}</tbody></table></div>
      )}

      <h2>Dia a dia</h2>
      {cap && (
        <div className="tablewrap card"><table>
          <thead><tr><th>Dia</th><th>Disponível</th><th>Livre</th><th>Horários</th><th>Situação</th></tr></thead>
          <tbody>{cap.days.filter((d) => d.available_minutes > 0 || d.closed_reason).map((d) => (
            <tr key={d.date}><td>{d.date.slice(8)}/{d.date.slice(5, 7)}</td><td>{formatMinutes(d.available_minutes)}</td><td>{formatMinutes(d.remaining_minutes)}</td><td>{d.slots_remaining}</td><td>{d.closed_reason ?? "Aberto"}</td></tr>))}</tbody></table></div>
      )}

      <div className="grid2" style={{ marginTop: 16 }}>
        <div className="card">
          <h2>Feriados {year}</h2>
          <p><button className="secondary" disabled={busy} onClick={() => run(async () => { const r = await call<{ created: number }>(`/api/v1/holidays/suggest?year=${year}`, { method: "POST" }); setNotice(`${r.created} feriados nacionais sugeridos. Confirme os que valem para você.`); })}>Sugerir feriados nacionais</button></p>
          <p className="muted">Sugestões não fecham a agenda até você confirmar. Feriados estaduais e municipais precisam ser cadastrados.</p>
          <ul style={{ paddingLeft: 18 }}>{holidays.map((h) => (
            <li key={h.id}><label style={{ fontWeight: 400, margin: 0 }}>
              <input type="checkbox" checked={h.confirmed} style={{ width: "auto", minHeight: 0 }} onChange={(e) => run(() => call(`/api/v1/holidays/${h.id}`, { method: "PATCH", body: { confirmed: e.target.checked } }))} />{" "}
              {h.date.slice(8)}/{h.date.slice(5, 7)} · {h.name} {h.confirmed ? "(confirmado)" : "(sugerido)"}</label></li>))}</ul>
          <form onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); run(() => call("/api/v1/holidays", { method: "POST", body: { date: f.get("date"), name: f.get("name"), scope: "MUNICIPAL" } })); e.currentTarget.reset(); }}>
            <label htmlFor="hd">Novo feriado local</label>
            <input id="hd" name="date" type="date" required /><input aria-label="Nome do feriado" name="name" required placeholder="Nome" style={{ marginTop: 8 }} />
            <p><button type="submit" disabled={busy}>Adicionar feriado</button></p>
          </form>
        </div>
        <div className="card">
          <h2>Férias e licenças {year}</h2>
          <ul style={{ paddingLeft: 18 }}>{leaves.map((l) => (
            <li key={l.id}>{l.staff_role === "PHYSICIAN" ? "Médico" : "Secretária"}: {l.start_date.split("-").reverse().join("/")} a {l.end_date.split("-").reverse().join("/")}{" "}
              <button className="secondary" aria-label="Remover período" onClick={() => run(() => call(`/api/v1/leaves/${l.id}`, { method: "DELETE" }))}>Remover</button></li>))}</ul>
          <form onSubmit={onLeave}>
            <label htmlFor="lr">Quem</label>
            <select id="lr" name="role"><option value="PHYSICIAN">Médico (tira capacidade)</option><option value="SECRETARY">Secretária (afeta captação)</option></select>
            <label htmlFor="lf">De</label><input id="lf" name="from" type="date" required />
            <label htmlFor="lt">Até</label><input id="lt" name="to" type="date" required />
            <p><button type="submit" disabled={busy}>Adicionar período</button></p>
          </form>
        </div>
      </div>

      <form className="card" onSubmit={onLimits} style={{ marginTop: 16 }}>
        <h2>Limites de jornada e descanso ({month})</h2>
        <div className="grid2">
          <div><label htmlFor="la">Máx. horas ativas</label><input id="la" name="active" inputMode="decimal" placeholder="160" /></div>
          <div><label htmlFor="lc">Máx. horas comprometidas</label><input id="lc" name="committed" inputMode="decimal" placeholder="220" /></div>
          <div><label htmlFor="lr2">Descanso mínimo após plantão (h)</label><input id="lr2" name="rest" inputMode="decimal" placeholder="11" /></div>
          <div><label htmlFor="ln">Máx. plantões noturnos</label><input id="ln" name="night" type="number" min="0" max="31" /></div>
        </div>
        <label htmlFor="lo">Folgas (datas AAAA-MM-DD separadas por vírgula)</label><input id="lo" name="off" placeholder="2026-10-12, 2026-10-13" />
        <p className="muted">Limites que você informa. O app não substitui regras legais ou contratuais.</p>
        <p><button type="submit" disabled={busy}>Salvar limites</button></p>
      </form>
    </>
  );
}
