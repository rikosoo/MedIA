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
