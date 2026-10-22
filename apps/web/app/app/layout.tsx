"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, type Me } from "@/lib/api";
import { SessionContext } from "./session";

// Rotas do PRD §4. Só as marcadas `ready` existem nesta entrega (Sprints 0–10).
const NAV = [
  { href: "/app/dashboard", label: "Início", ready: true },
  { href: "/app/goals", label: "Minha Meta", ready: true },
  { href: "/app/optimizer", label: "Otimizador Misto", ready: true },
  { href: "/app/work", label: "Trabalhos & Rendas", ready: true },
  { href: "/app/clinic", label: "Consultório", ready: true },
  { href: "/app/schedule", label: "Agenda & Capacidade", ready: true },
  { href: "/app/finances", label: "Receitas & Despesas", ready: true },
  { href: "/app/taxes", label: "Tributos PF/PJ" },
  { href: "/app/acquisition", label: "Captação", ready: true },
  { href: "/app/forecasts", label: "Previsões", ready: true },
  { href: "/app/assistant", label: "Consultor IA", ready: true },
  { href: "/app/reports", label: "Relatórios", ready: true },
  { href: "/app/team", label: "Equipe" },
  { href: "/app/billing", label: "Planos & Cobrança", ready: true },
  { href: "/app/settings", label: "Configurações" },
];

const BOTTOM = [
  { href: "/app/dashboard", label: "Início" },
  { href: "/app/goals", label: "Meta" },
  { href: "/app/work", label: "Trabalhos" },
  { href: "/app/schedule", label: "Agenda" },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "error">("loading");
  const [unread, setUnread] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    api<Me>("/api/v1/me")
      .then(setMe)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
        else setState("error");
      });
  }, [router]);

  // Sino: gera avisos uma vez por sessão do navegador (só para quem ligou) e mostra os não lidos.
  useEffect(() => {
    if (!me) return;
    const orgId = me.organizations[0].id;
    (async () => {
      try {
        if (!sessionStorage.getItem("fm_notif_checked")) {
          sessionStorage.setItem("fm_notif_checked", "1");
          await api("/api/v1/notifications/generate", { method: "POST", csrf: me.csrf_token, orgId, body: {} });
        }
        const r = await api<{ unread_count: number }>("/api/v1/notifications?limit=1", { orgId });
        setUnread(r.unread_count);
      } catch {
        /* notificações são opcionais: falhas não atrapalham o app */
      }
    })();
  }, [me, pathname]);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  if (state === "error") return <main role="alert">Não foi possível carregar sua sessão. Tente novamente.</main>;
  if (!me) return <main aria-busy="true">Carregando…</main>;

  const org = me.organizations[0];

  async function logout() {
    await api("/api/v1/auth/logout", { method: "POST", csrf: me!.csrf_token });
    router.replace("/login");
  }

  return (
    <SessionContext.Provider value={{ me, org }}>
      <a className="skip" href="#conteudo">Ir para o conteúdo</a>
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
              <Link href="/app/notifications" aria-label={unread > 0 ? `Avisos: ${unread} não lidos` : "Avisos"}>
                Avisos{unread > 0 ? ` (${unread})` : ""}
              </Link>{" · "}
              {me.name} · <span className="muted">{org.role}</span>{" "}
              <button className="secondary" onClick={logout}>
                Sair
              </button>
            </span>
          </header>
          <main id="conteudo" tabIndex={-1}>
            {children}
          </main>
        </div>
      </div>
      {menuOpen && (
        <div className="menusheet" id="menu-sheet" role="dialog" aria-label="Menu">
          {NAV.map((n) =>
            n.ready ? (
              <Link key={n.href} href={n.href} aria-current={pathname === n.href ? "page" : undefined}>
                {n.label}
              </Link>
            ) : (
              <a key={n.href} className="soon" aria-disabled="true">
                {n.label} <small>(em breve)</small>
              </a>
            ),
          )}
        </div>
      )}
      <nav className="bottomnav" aria-label="Atalhos">
        {BOTTOM.map((n) => (
          <Link key={n.href} href={n.href} aria-current={pathname === n.href ? "page" : undefined}>
            {n.label}
          </Link>
        ))}
        <button type="button" aria-expanded={menuOpen} aria-controls="menu-sheet" onClick={() => setMenuOpen((v) => !v)}>
          Menu
        </button>
      </nav>
    </SessionContext.Provider>
  );
}
