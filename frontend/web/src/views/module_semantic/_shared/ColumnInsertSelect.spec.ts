import { mount } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import type { CatColumn } from "@/api/module_semantic/catalog";
import ColumnInsertSelect from "./ColumnInsertSelect.vue";

const columns: CatColumn[] = [
  { id: 1, ordinal: 1, name: "amt", col_type: "decimal", is_nullable: false },
];

test("选中列后把插入结果整串 emit 给父级", async () => {
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => cb());
  const ta = document.createElement("textarea");
  ta.value = "SUM()";
  ta.selectionStart = 4;
  ta.selectionEnd = 4;
  const w = mount(ColumnInsertSelect, { props: { columns, getTarget: () => ta } });
  await (w.vm as any).onChange("amt");
  expect(w.emitted("insert")?.at(-1)).toEqual(["SUM(amt)"]);
  vi.unstubAllGlobals();
});

test("目标缺失时不 emit 插入(提示用户)", async () => {
  const w = mount(ColumnInsertSelect, { props: { columns, getTarget: () => null } });
  await (w.vm as any).onChange("amt");
  expect(w.emitted("insert")).toBeUndefined();
});

test("取消选择(空值)不 emit", async () => {
  const w = mount(ColumnInsertSelect, { props: { columns, getTarget: () => null } });
  await (w.vm as any).onChange(undefined);
  expect(w.emitted("insert")).toBeUndefined();
});
