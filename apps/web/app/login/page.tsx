"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const f = new FormData(e.currentTarget);
    try {
      if (mode === "login") {
        await api("/api/v1/auth/login", {
          method: "POST",
          body: { email: f.get("email"), password: f.get("password") },
        });
      } else {
        await api("/api/v1/auth/register", {
          method: "POST",
          body: {
            email: f.get("email"),
            password: f.get("password"),
            name: f.get("name"),
            organization_name: f.get("organization_name"),
          },
        });
      }
      router.push("/app/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Não foi possível conectar ao servidor.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth">
      <h1>FinanceMed AI</h1>
      <p className="muted">Planejamento financeiro e de agenda para médicos.</p>
      <form className="card" onSubmit={onSubmit} aria-labelledby="form-title">
        <h2 id="form-title">{mode === "login" ? "Entrar" : "Criar conta"}</h2>
        {mode === "register" && (
          <>
            <label htmlFor="name">Seu nome</label>
            <input id="name" name="name" required autoComplete="name" />
            <label htmlFor="org">Clínica ou uso pessoal</label>
            <input id="org" name="organization_name" required />
          </>
        )}
        <label htmlFor="email">E-mail</label>
        <input id="email" name="email" type="email" required autoComplete="email" />
        <label htmlFor="password">Senha {mode === "register" && "(mínimo 10 caracteres)"}</label>
        <input
          id="password"
          name="password"
          type="password"
          required
          minLength={mode === "register" ? 10 : undefined}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <p>
          <button type="submit" disabled={loading}>
            {loading ? "Aguarde…" : mode === "login" ? "Entrar" : "Criar conta"}
          </button>{" "}
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setError(null);
              setMode(mode === "login" ? "register" : "login");
            }}
          >
            {mode === "login" ? "Criar conta" : "Já tenho conta"}
          </button>
        </p>
      </form>
    </div>
  );
}
