import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const projectRoot = resolve(process.cwd());
const packageJsonPath = resolve(projectRoot, "package.json");
const frontendGitignorePath = resolve(projectRoot, ".gitignore");
const autoImportsPath = resolve(projectRoot, "src/types/import/auto-imports.d.ts");
const componentsPath = resolve(projectRoot, "src/types/import/components.d.ts");

describe("frontend build contract", () => {
  it("keeps plugin declarations available before vue-tsc", () => {
    const packageJson = JSON.parse(readFileSync(packageJsonPath, "utf-8")) as {
      scripts?: Record<string, string>;
    };
    const gitignore = readFileSync(frontendGitignorePath, "utf-8");
    const autoImports = readFileSync(autoImportsPath, "utf-8");
    const components = readFileSync(componentsPath, "utf-8");

    expect(packageJson.scripts?.build).toBe("vue-tsc --noEmit && vite build");
    expect(gitignore).not.toContain("src/types/import/auto-imports.d.ts");
    expect(gitignore).not.toContain("src/types/import/components.d.ts");
    expect(existsSync(autoImportsPath)).toBe(true);
    expect(existsSync(componentsPath)).toBe(true);
    expect(autoImports).toContain("const ref: typeof import('vue').ref");
    expect(autoImports).toContain("const storeToRefs: typeof import('pinia').storeToRefs");
    expect(autoImports).toContain("const useScroll: typeof import('@vueuse/core').useScroll");
    expect(components).toContain("export interface GlobalComponents");
  });
});
