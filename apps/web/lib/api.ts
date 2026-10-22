const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Org = { id: string; name: string; role: string };
export type Me = { id: string; email: string; name: string; csrf_token: string; organizations: Org[] };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  opts: { method?: string; body?: unknown; csrf?: string; orgId?: string } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.csrf) headers["X-CSRF-Token"] = opts.csrf;
  if (opts.orgId) headers["X-Organization-Id"] = opts.orgId;
  const res = await fetch(`${BASE}${path}`, {
    method: opts.method ?? "GET",
    headers,
    credentials: "include",
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : "Dados inválidos";
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}
