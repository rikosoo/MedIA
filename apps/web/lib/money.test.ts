import { describe, expect, it } from "vitest";
import { formatBRL, parseBRLToCents } from "./money";

describe("money", () => {
  it("formata centavos em pt-BR preservando o sinal", () => {
    expect(formatBRL(123456)).toBe("R$ 1.234,56");
    expect(formatBRL(-5000)).toBe("-R$ 50,00");
    expect(formatBRL(5)).toBe("R$ 0,05");
  });
  it("converte texto em centavos sem float", () => {
    expect(parseBRLToCents("R$ 1.234,56")).toBe(123456);
    expect(parseBRLToCents("0.10")).toBe(10);
    expect(parseBRLToCents("abc")).toBeNull();
    expect(parseBRLToCents("1,234")).toBeNull();
  });
});
