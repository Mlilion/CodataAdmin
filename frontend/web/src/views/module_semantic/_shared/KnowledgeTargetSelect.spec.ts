import { mount, flushPromises } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
vi.mock("@/api/module_semantic/entity", () => ({
  default: {
    listEntity: vi
      .fn()
      .mockResolvedValue({ data: { data: { items: [{ id: 7, code: "order", name: "订单" }], total: 1 } } }),
  },
}));
vi.mock("@/api/module_semantic/dimension", () => ({ default: { listDimension: vi.fn().mockResolvedValue({ data: { data: { items: [], total: 0 } } }) } }));
vi.mock("@/api/module_semantic/indicator", () => ({ default: { listIndicator: vi.fn().mockResolvedValue({ data: { data: { items: [], total: 0 } } }) } }));
vi.mock("@/api/module_semantic/join", () => ({ default: { listJoin: vi.fn().mockResolvedValue({ data: { data: { items: [], total: 0 } } }) } }));
vi.mock("@/api/module_semantic/knowledge", () => ({ default: { listKnowledge: vi.fn().mockResolvedValue({ data: { data: { items: [], total: 0 } } }) } }));
import KnowledgeTargetSelect from "./KnowledgeTargetSelect.vue";

test("按 targetType=entity 加载并 emit id+label", async () => {
  const w = mount(KnowledgeTargetSelect, { props: { targetType: "entity", modelValue: undefined } });
  await flushPromises();
  await (w.vm as any).onChange(7);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([7]);
  expect(w.emitted("update:label")?.at(-1)).toEqual(["订单 (order)"]);
});

test("targetType=field 禁用且不加载", async () => {
  const w = mount(KnowledgeTargetSelect, { props: { targetType: "field" } });
  await flushPromises();
  expect((w.vm as any).options).toEqual([]);
});

test("编辑态挂载(targetType+modelValue 均有初值)不应 emit update:modelValue(undefined)", async () => {
  const w = mount(KnowledgeTargetSelect, { props: { targetType: "entity", modelValue: 7 } });
  await flushPromises();
  expect(w.emitted("update:modelValue")).toBeUndefined();
});

test("targetType 变更清空已选", async () => {
  const w = mount(KnowledgeTargetSelect, { props: { targetType: "entity", modelValue: 7 } });
  await flushPromises();
  await w.setProps({ targetType: "dimension" });
  await flushPromises();
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([undefined]);
});
