"use client";

import { createContext, useContext } from "react";
import type { Me, Org } from "@/lib/api";

export type Session = { me: Me; org: Org };
export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession fora do shell autenticado");
  return s;
}
