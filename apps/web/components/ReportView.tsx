import { formatBRL } from "@/lib/money";
import { formatMinutes } from "@/lib/time";

type Cell = string | number | null | { cents?: number; bps?: number; minutes?: number };
export type ReportDoc = {
  title: string; subtitle: string; generated_at: string; disclaimer: string;
  sections: { heading: string; kpis: [string, Cell][]; tables: { title: string; columns: string[]; rows: Cell[][] }[]; notes: string[] }[];
};

export function cell(c: Cell): string {
  if (c !== null && typeof c === "object") {
    if (c.cents !== undefined) return formatBRL(c.cents);
    if (c.bps !== undefined) return `${Math.round(c.bps / 100)}%`;
    if (c.minutes !== undefined) return formatMinutes(c.minutes);
  }
  return c === null ? "" : String(c);
}

export function ReportView({ doc }: { doc: ReportDoc }) {
  return (
    <article aria-label="Pré-visualização do relatório">
      <h2>{doc.title}</h2>
      <p className="muted">{doc.subtitle} · gerado em {doc.generated_at}</p>
      {doc.sections.map((s) => (
        <section key={s.heading} style={{ marginTop: 16 }}>
          <h3>{s.heading}</h3>
          {s.kpis.length > 0 && (
            <dl style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "4px 16px" }}>
              {s.kpis.map(([k, v]) => (<><dt key={`k-${k}`}>{k}</dt><dd key={`v-${k}`} style={{ margin: 0, fontWeight: 600 }}>{cell(v)}</dd></>))}
            </dl>
          )}
          {s.tables.map((t) => (
            <div key={t.title} className="tablewrap">
              <h4>{t.title}</h4>
              <table>
                <thead><tr>{t.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>
                  {t.rows.length === 0 ? <tr><td colSpan={t.columns.length} className="muted">Sem registros</td></tr> : t.rows.slice(0, 60).map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{cell(c)}</td>)}</tr>)}
                </tbody>
              </table>
              {t.rows.length > 60 && <p className="muted">Mostrando 60 de {t.rows.length} linhas; o arquivo traz todas.</p>}
            </div>
          ))}
          {s.notes.map((n, i) => <p key={i} className="muted">• {n}</p>)}
        </section>
      ))}
      <p className="muted" style={{ marginTop: 16 }}>{doc.disclaimer}</p>
    </article>
  );
}
