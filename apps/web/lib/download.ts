const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Baixa um arquivo autenticado (cookie + organização) e o entrega ao navegador. */
export async function downloadFile(path: string, orgId: string, filename: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { credentials: "include", headers: { "X-Organization-Id": orgId } });
  if (!res.ok) throw new Error(String(res.status));
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export const publicUrl = (path: string) => `${BASE}${path}`;
