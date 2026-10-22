"use client";

import { formatBRL } from "@/lib/money";
import { useWidth } from "./useWidth";

export type FMonth = {
  month: string; net_cents: { conservative: number; base: number; optimistic: number }; goal_target_cents: number | null;
};

const H = 240;
const PAD = { l: 8, r: 8, t: 14, b: 26 };

/** Faixa conservador–otimista com a linha do cenário base. A faixa é incerteza, não promessa. */
export function ForecastChart({ months }: { months: FMonth[] }) {
  const [ref, W] = useWidth();
  const vals = months.flatMap((m) => [m.net_cents.conservative, m.net_cents.base, m.net_cents.optimistic, m.goal_target_cents ?? 0, 0]);
  const max = Math.max(...vals);
  const min = Math.min(...vals);
  const span = Math.max(max - min, 1);
  const y = (v: number) => PAD.t + ((max - v) / span) * (H - PAD.t - PAD.b);
  const step = (W - PAD.l - PAD.r) / Math.max(months.length - 1, 1);
  const every = Math.max(1, Math.ceil(40 / Math.max(step, 1)));
  const x = (i: number) => PAD.l + (months.length === 1 ? (W - PAD.l - PAD.r) / 2 : i * step);
  const upper = months.map((m, i) => `${x(i)},${y(m.net_cents.optimistic)}`);
  const lower = months.map((m, i) => `${x(i)},${y(m.net_cents.conservative)}`).reverse();
  const base = months.map((m, i) => `${x(i)},${y(m.net_cents.base)}`).join(" ");

  return (
    <div ref={ref}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Previsão do resultado líquido: faixa entre o cenário conservador e o otimista, e linha do cenário base" style={{ display: "block", maxWidth: "100%" }}>
        <line x1={PAD.l} x2={W - PAD.r} y1={y(0)} y2={y(0)} stroke="var(--border)" />
        <polygon points={[...upper, ...lower].join(" ")} fill="var(--accent)" opacity={0.22} />
        <polyline points={base} fill="none" stroke="var(--primary-chart)" strokeWidth={2.5} />
        {months.map((m, i) => (
          <g key={m.month}>
            <circle cx={x(i)} cy={y(m.net_cents.base)} r={3.5} fill="var(--primary-chart)"><title>{`${m.month} base: ${formatBRL(m.net_cents.base)}`}</title></circle>
            {m.goal_target_cents !== null && <line x1={x(i) - 8} x2={x(i) + 8} y1={y(m.goal_target_cents)} y2={y(m.goal_target_cents)} stroke="var(--text)" strokeDasharray="3 2"><title>{`${m.month} meta: ${formatBRL(m.goal_target_cents)}`}</title></line>}
            {i % every === 0 && <text x={x(i)} y={H - 8} textAnchor="middle" fontSize="12" fill="var(--muted)">{m.month.slice(5)}/{m.month.slice(2, 4)}</text>}
          </g>
        ))}
      </svg>
      <p className="muted" style={{ fontSize: 14 }}>
        <span style={{ color: "var(--primary-chart)" }}>━</span> Base <span style={{ color: "var(--accent)" }}>▮</span> Faixa conservador–otimista <span>┄</span> Meta
      </p>
    </div>
  );
}
