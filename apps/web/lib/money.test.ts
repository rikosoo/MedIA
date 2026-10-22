import { describe, expect, it } from "vitest";
import { bpsToPercent, formatBRL, parseBRLToCents, percentToBps } from "./money";

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
  it("converte percentual em pontos-base sem float", () => {
    expect(percentToBps("76")).toBe(7600);
    expect(percentToBps("7,5")).toBe(750);
    expect(percentToBps("100")).toBe(10000);
    expect(percentToBps("100,01")).toBeNull();
    expect(percentToBps("-1")).toBeNull();
    expect(percentToBps("120", 12_000)).toBe(12_000);
    expect(percentToBps("120.01", 12_000)).toBeNull();
    expect(bpsToPercent(7600)).toBe("76,00%");
  });
});
