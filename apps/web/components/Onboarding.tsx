"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Step = { key: string; label: string; route: string; done: boolean };
type Data = { steps: Step[]; done: number; total: number; complete: boolean };

export function Onboarding({ orgId }: { orgId: string }) {
  const [d, setD] = useState<Data | null>(null);
  useEffect(() => { api<Data>("/api/v1/onboarding", { orgId }).then(setD).catch(() => setD(null)); }, [orgId]);
  if (!d || d.complete || d.total === 0) return null;
  return (
    <section className="card" aria-labelledby="onb-title">
      <h2 id="onb-title">Primeiros passos ({d.done} de {d.total})</h2>
      <progress max={d.total} value={d.done} aria-label="Progresso dos primeiros passos" />
      <ul>
        {d.steps.map((s) => (
          <li key={s.key}>
            {s.done ? <span aria-label="Concluído">✓ </span> : <span aria-label="Pendente">○ </span>}
            {s.done ? <span className="muted">{s.label}</span> : <Link href={s.route}>{s.label}</Link>}
          </li>
        ))}
      </ul>
    </section>
  );
}
