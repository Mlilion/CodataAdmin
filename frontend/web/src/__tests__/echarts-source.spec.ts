import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const sourcePath = resolve(process.cwd(), "src/components/ECharts/index.vue");

describe("FaECharts source contract", () => {
  it("explicitly imports every Vue API used by the component", () => {
    const source = readFileSync(sourcePath, "utf-8");

    expect(source).toContain(
      'import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";'
    );
  });
});
