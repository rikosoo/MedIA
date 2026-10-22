import { formatBRL } from "@/lib/money";

export type SeriesPoint = {
  month: string;
  projected_cents: number;
  realized_cents: number;
  target_cents: number | null;
};

const W = 640;
const H = 220;
const PAD = { l: 8, r: 8, t: 12, b: 28 };

/** Barras agrupadas projetado (marinho) x realizado (esmeralda). Valores negativos descem da linha-base. */
export function MonthBars({ series }: { series: SeriesPoint[] }) {
  const vals = series.flatMap((p) => [p.projected_cents, p.realized_cents, p.target_cents ?? 0, 0]);
  const max = Math.max(...vals);
  const min = Math.min(...vals);
  const span = Math.max(max - min, 1);
  const y = (v: number) => PAD.t + ((max - v) / span) * (H - PAD.t - PAD.b);
  const base = y(0);
  const slot = (W - PAD.l - PAD.r) / series.length;
  const bw = Math.max(slot / 3, 4);

  const bar = (v: number, x: number, color: string, label: string) => {
    const top = Math.min(y(v), base);
    return <rect x={x} y={top} width={bw} height={Math.max(Math.abs(y(v) - base), v === 0 ? 0 : 1)} fill={color} rx={2}><title>{label}</title></rect>;
  };

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Resultado projetado e realizado nos últimos 12 meses" style={{ width: "100%", height: "auto" }}>
        <line x1={PAD.l} x2={W - PAD.r} y1={base} y2={base} stroke="var(--border)" />
        {series.map((p, i) => {
          const x0 = PAD.l + i * slot + (slot - 2 * bw - 2) / 2;
          return (
            <g key={p.month}>
              {bar(p.projected_cents, x0, "var(--primary-chart)", `${p.month} projetado: ${formatBRL(p.projected_cents)}`)}
              {bar(p.realized_cents, x0 + bw + 2, "var(--accent)", `${p.month} realizado: ${formatBRL(p.realized_cents)}`)}
              {p.target_cents !== null && (
                <line x1={x0 - 2} x2={x0 + 2 * bw + 4} y1={y(p.target_cents)} y2={y(p.target_cents)} stroke="var(--text)" strokeDasharray="3 2"><title>{`${p.month} meta: ${formatBRL(p.target_cents)}`}</title></line>
              )}
              <text x={PAD.l + i * slot + slot / 2} y={H - 10} textAnchor="middle" fontSize="10" fill="var(--muted)">{p.month.slice(5)}/{p.month.slice(2, 4)}</text>
            </g>
          );
        })}
      </svg>
      <p className="muted" style={{ fontSize: 14 }}>
        <span style={{ color: "var(--primary-chart)" }}>■</span> Projetado{" "}
        <span style={{ color: "var(--accent)" }}>■</span> Realizado <span>┄</span> Meta
      </p>
      <details>
        <summary>Ver valores em tabela</summary>
        <div className="tablewrap">
          <table>
            <thead><tr><th>Mês</th><th>Projetado</th><th>Realizado</th><th>Meta</th></tr></thead>
            <tbody>
              {series.map((p) => (
                <tr key={p.month}>
                  <td>{p.month}</td><td>{formatBRL(p.projected_cents)}</td><td>{formatBRL(p.realized_cents)}</td>
                  <td>{p.target_cents === null ? "—" : formatBRL(p.target_cents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
