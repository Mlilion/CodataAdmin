import { mount, flushPromises } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import EntityAPI from "@/api/module_semantic/entity";
vi.mock("@/api/module_semantic/entity", () => ({
  default: {
    detailEntity: vi
      .fn()
      .mockResolvedValue({ data: { data: { fields: [{ code: "pay_amount", name: "实付金额" }] } } }),
  },
}));
import FieldSelect from "./FieldSelect.vue";

test("按 entityId 加载字段并 emit fieldCode", async () => {
  const w = mount(FieldSelect, { props: { modelValue: undefined, entityId: 7 } });
  await flushPromises();
  expect((w.vm as any).fields).toHaveLength(1);
  (w.vm as any).onChange("pay_amount");
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual(["pay_amount"]);
});

test("编辑态挂载(entityId+modelValue 均有初值)不应 emit update:modelValue(undefined)", async () => {
  const w = mount(FieldSelect, { props: { modelValue: "pay_amount", entityId: 7 } });
  await flushPromises();
  expect(w.emitted("update:modelValue")).toBeUndefined();
});

test("entityId 从 7 变到 8 时清空已选(emit undefined)", async () => {
  const w = mount(FieldSelect, { props: { modelValue: "pay_amount", entityId: 7 } });
  await flushPromises();
  await w.setProps({ entityId: 8 });
  await flushPromises();
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([undefined]);
});

test("load 失败时 fields 为空数组且不抛异常", async () => {
  (EntityAPI.detailEntity as any).mockRejectedValueOnce(new Error("network"));
  const w = mount(FieldSelect, { props: { modelValue: undefined, entityId: 9 } });
  await flushPromises();
  expect((w.vm as any).fields).toEqual([]);
});
