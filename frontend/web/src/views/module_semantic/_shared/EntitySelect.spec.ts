import { mount, flushPromises } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
vi.mock("@/api/module_semantic/entity", () => ({
  default: {
    listEntity: vi
      .fn()
      .mockResolvedValue({ data: { data: { items: [{ id: 7, code: "order", name: "订单" }], total: 1 } } }),
  },
}));
import EntityAPI from "@/api/module_semantic/entity";
import EntitySelect from "./EntitySelect.vue";

test("加载实体并 emit id+code", async () => {
  const w = mount(EntitySelect, { props: { modelValue: undefined } });
  await flushPromises();
  await (w.vm as any).onChange(7);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([7]);
  expect(w.emitted("update:code")?.at(-1)).toEqual(["order"]);
});

test("不传 excludeIds 时选项为全量", async () => {
  const w = mount(EntitySelect, { props: { modelValue: undefined } });
  await flushPromises();
  expect((w.vm as any).filteredOptions.map((e: { id: number }) => e.id)).toEqual([7]);
});

test("excludeIds 过滤掉已入模实体", async () => {
  vi.mocked(EntityAPI.listEntity).mockResolvedValueOnce({
    data: {
      data: {
        items: [
          { id: 7, code: "order", name: "订单" },
          { id: 8, code: "user", name: "用户" },
        ],
        total: 2,
      },
    },
  } as never);
  const w = mount(EntitySelect, { props: { modelValue: undefined, excludeIds: [7] } });
  await flushPromises();
  expect((w.vm as any).filteredOptions.map((e: { id: number }) => e.id)).toEqual([8]);
  expect((w.vm as any).options).toHaveLength(2);
});
