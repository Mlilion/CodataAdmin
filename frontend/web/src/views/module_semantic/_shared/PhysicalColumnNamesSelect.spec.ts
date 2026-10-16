import { mount } from "@vue/test-utils";
import { expect, test } from "vitest";
import type { CatColumn } from "@/api/module_semantic/catalog";
import PhysicalColumnNamesSelect from "./PhysicalColumnNamesSelect.vue";

const columns: CatColumn[] = [
  { id: 1, ordinal: 1, name: "order_id", col_type: "bigint", is_nullable: false },
  { id: 2, ordinal: 2, name: "order_date", col_type: "date", is_nullable: true, comment: "下单日期" },
  { id: 3, ordinal: 3, name: "amount", col_type: "decimal(18,2)", is_nullable: true },
];

test("多选:逗号连接序列化与解析", () => {
  const w = mount(PhysicalColumnNamesSelect, {
    props: { modelValue: "order_id", columns, multiple: true },
  });
  expect((w.vm as any).selectedNames).toEqual(["order_id"]);
  (w.vm as any).onChange(["order_id", "amount"]);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual(["order_id,amount"]);
  (w.vm as any).onChange([]);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([undefined]);
});

test("单选:直接 emit 列名,清空 emit undefined", () => {
  const w = mount(PhysicalColumnNamesSelect, { props: { modelValue: undefined, columns } });
  (w.vm as any).onChange("order_date");
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual(["order_date"]);
  (w.vm as any).onChange(undefined);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([undefined]);
});

test("回显保值:modelValue 中不在列清单的名字追加为额外选项", () => {
  const w = mount(PhysicalColumnNamesSelect, {
    props: { modelValue: "order_id,legacy_col", columns, multiple: true },
  });
  expect((w.vm as any).extraNames).toEqual(["legacy_col"]);
});

test("preferTime 把日期/时间类型列排前", () => {
  const w = mount(PhysicalColumnNamesSelect, {
    props: { modelValue: undefined, columns, preferTime: true },
  });
  const sorted = (w.vm as any).sortedColumns.map((c: CatColumn) => c.name);
  expect(sorted[0]).toBe("order_date");
  expect(sorted).toHaveLength(3);
});
