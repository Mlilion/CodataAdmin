import { mount, flushPromises } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
vi.mock("@/api/module_semantic/domain", () => ({
  default: {
    listDomain: vi
      .fn()
      .mockResolvedValue({ data: { data: { items: [{ id: 1, code: "trade", name: "交易域" }], total: 1 } } }),
  },
}));
import DomainSelect from "./DomainSelect.vue";

test("加载业务域并 emit code", async () => {
  const w = mount(DomainSelect, { props: { modelValue: undefined } });
  await flushPromises();
  expect((w.vm as any).options).toHaveLength(1);
  (w.vm as any).onChange("trade");
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual(["trade"]);
});
