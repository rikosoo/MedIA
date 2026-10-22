/** Dinheiro trafega em centavos inteiros; formata sem passar por float de reais. */
export function formatBRL(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const reais = Math.floor(abs / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${sign}R$ ${reais},${(abs % 100).toString().padStart(2, "0")}`;
}

/** "1.234,56" ou "1234.56" -> centavos; null se inválido. */
export function parseBRLToCents(input: string): number | null {
  let t = input.replace(/R\$|\s/g, "");
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  const [r, c = ""] = t.split(".");
  return Number(r) * 100 + Number(c.padEnd(2, "0"));
}

/** "76" ou "7,5" (%) -> pontos-base inteiros; null se inválido ou fora de 0–100. */
export function percentToBps(input: string): number | null {
  const t = input.trim().replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  const [i, f = ""] = t.split(".");
  const bps = Number(i) * 100 + Number(f.padEnd(2, "0"));
  return bps <= 10_000 ? bps : null;
}

export function bpsToPercent(bps: number): string {
  return `${(bps / 100).toFixed(2).replace(".", ",")}%`;
}
