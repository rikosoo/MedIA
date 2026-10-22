"use client";

import { useSession } from "../session";

export default function Dashboard() {
  const { me } = useSession();
  return (
    <>
      <h1>Olá, {me.name}</h1>
      <div className="card">
        <h2>Comece cadastrando seus trabalhos</h2>
        <p className="muted">
          Metas, otimizador e projeções chegam nas próximas sprints. Valores de tributos são sempre
          estimativas não validadas.
        </p>
      </div>
    </>
  );
}
