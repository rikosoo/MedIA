import { describe, expect, it } from "vitest";
import { formatMinutes, toOffsetISO } from "./time";

describe("time", () => {
  it("aplica o offset do fuso da organização, não o do navegador", () => {
    expect(toOffsetISO("2026-10-06T07:00", "America/Sao_Paulo")).toBe("2026-10-06T07:00:00-03:00");
    expect(toOffsetISO("2026-10-06T07:00", "UTC")).toBe("2026-10-06T07:00:00+00:00");
    expect(toOffsetISO("2026-01-15T10:30", "Asia/Kolkata")).toBe("2026-01-15T10:30:00+05:30");
  });
  it("rejeita entrada inválida", () => {
    expect(() => toOffsetISO("06/10/2026", "UTC")).toThrow();
  });
  it("formata minutos", () => {
    expect(formatMinutes(150)).toBe("2h30");
    expect(formatMinutes(45)).toBe("45 min");
    expect(formatMinutes(600)).toBe("10h");
  });
});
