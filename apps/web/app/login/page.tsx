"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [needOtp, setNeedOtp] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const f = new FormData(e.currentTarget);
    try {
      if (mode === "login") {
        await api("/api/v1/auth/login", {
          method: "POST",
          body: { email: f.get("email"), password: f.get("password"), otp: f.get("otp") || null },
        });
      } else {
        await api("/api/v1/auth/register", {
          method: "POST",
          body: {
            email: f.get("email"),
            password: f.get("password"),
            name: f.get("name"),
            organization_name: f.get("organization_name"),
            accept_terms: f.get("accept_terms") === "on",
          },
        });
      }
      router.push("/app/dashboard");
    } catch (err) {
      if (err instanceof ApiError && err.message === "MFA_REQUIRED") {
        setNeedOtp(true);
        setError("Informe o código do app autenticador (ou um código de recuperação).");
      } else setError(err instanceof ApiError ? err.message : "Não foi possível conectar ao servidor.");
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
        <label htmlFor="password">Senha {mode === "register" && "(mínimo 6, com maiúscula, número e símbolo)"}</label>
        <input
          id="password"
          name="password"
          type="password"
          required
          minLength={mode === "register" ? 6 : undefined}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
        />
        {mode === "login" && needOtp && (
          <>
            <label htmlFor="otp">Código de verificação</label>
            <input id="otp" name="otp" inputMode="numeric" autoComplete="one-time-code" required />
          </>
        )}
        {mode === "register" && (
          <label htmlFor="terms">
            <input id="terms" name="accept_terms" type="checkbox" required /> Li e aceito os{" "}
            <Link href="/termos">Termos de Uso</Link> e a <Link href="/privacidade">Política de Privacidade</Link>
          </label>
        )}
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
      <p className="muted"><small>As projeções são estimativas e não substituem contador ou assessoria tributária.</small></p>
    </div>
  );
}
