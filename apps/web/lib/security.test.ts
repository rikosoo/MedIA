import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    if (n === "node_modules" || n === ".next") return [];
    const p = join(dir, n);
    return statSync(p).isDirectory() ? sources(p) : /\.(tsx?|jsx?)$/.test(n) && !n.endsWith(".test.ts") ? [p] : [];
  });
}
const root = join(__dirname, "..");
const files = ["app", "components", "lib"].flatMap((d) => sources(join(root, d)));

describe("guardas de XSS no código da interface", () => {
  it("encontra os arquivos de código", () => expect(files.length).toBeGreaterThan(20));
  it.each([
    ["dangerouslySetInnerHTML", /dangerouslySetInnerHTML/],
    ["innerHTML/outerHTML", /\.(inner|outer)HTML\s*=/],
    ["document.write", /document\.write\(/],
    ["eval / new Function", /\beval\(|new Function\(/],
    ["insertAdjacentHTML", /insertAdjacentHTML/],
  ])("não usa %s", (_n, re) => {
    const bad = files.filter((f) => re.test(readFileSync(f, "utf8")));
    expect(bad).toEqual([]);
  });
});
