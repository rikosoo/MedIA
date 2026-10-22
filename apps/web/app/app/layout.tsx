"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, type Me } from "@/lib/api";
import { SessionContext } from "./session";

// Rotas do PRD §4. Só as marcadas `ready` existem nesta entrega (Sprints 0–5).
const NAV = [
  { href: "/app/dashboard", label: "Início", ready: true },
  { href: "/app/goals", label: "Minha Meta", ready: true },
  { href: "/app/optimizer", label: "Otimizador Misto", ready: true },
  { href: "/app/work", label: "Trabalhos & Rendas", ready: true },
  { href: "/app/clinic", label: "Consultório", ready: true },
  { href: "/app/schedule", label: "Agenda & Capacidade", ready: true },
  { href: "/app/finances", label: "Receitas & Despesas", ready: true },
  { href: "/app/taxes", label: "Tributos PF/PJ" },
  { href: "/app/acquisition", label: "Captação" },
  { href: "/app/forecasts", label: "Previsões" },
  { href: "/app/assistant", label: "Consultor IA" },
  { href: "/app/reports", label: "Relatórios" },
  { href: "/app/team", label: "Equipe" },
  { href: "/app/billing", label: "Planos & Cobrança" },
  { href: "/app/settings", label: "Configurações" },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "error">("loading");

  useEffect(() => {
    api<Me>("/api/v1/me")
      .then(setMe)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
        else setState("error");
      });
  }, [router]);

  if (state === "error") return <main role="alert">Não foi possível carregar sua sessão. Tente novamente.</main>;
  if (!me) return <main aria-busy="true">Carregando…</main>;

  const org = me.organizations[0];

  async function logout() {
    await api("/api/v1/auth/logout", { method: "POST", csrf: me!.csrf_token });
    router.replace("/login");
  }

  return (
    <SessionContext.Provider value={{ me, org }}>
      <div className="shell">
        <nav className="sidebar" aria-label="Principal">
          {NAV.map((n) =>
            n.ready ? (
              <Link key={n.href} href={n.href} aria-current={pathname === n.href ? "page" : undefined}>
                {n.label}
              </Link>
            ) : (
              <a key={n.href} className="soon" aria-disabled="true" title="Em breve">
                {n.label} <small>(em breve)</small>
              </a>
            ),
          )}
        </nav>
        <div>
          <header className="top">
            <strong>{org.name}</strong>
            <span>
              {me.name} · <span className="muted">{org.role}</span>{" "}
              <button className="secondary" onClick={logout}>
                Sair
              </button>
            </span>
          </header>
          <main>{children}</main>
        </div>
      </div>
    </SessionContext.Provider>
  );
}
