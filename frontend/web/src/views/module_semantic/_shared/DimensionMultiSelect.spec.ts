import { mount, flushPromises } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
vi.mock("@/api/module_semantic/dimension", () => ({
  default: {
    listDimension: vi.fn().mockResolvedValue({
      data: {
        data: {
          items: [
            { id: 3, code: "d1", name: "下单日期" },
            { id: 7, code: "d2", name: "地区" },
          ],
          total: 2,
        },
      },
    }),
  },
}));
import DimensionMultiSelect from "./DimensionMultiSelect.vue";

test("回显 JSON 串并 emit JSON 串", async () => {
  const w = mount(DimensionMultiSelect, { props: { modelValue: "[3]" } });
  await flushPromises();
  expect((w.vm as any).selected).toEqual([3]); // parse
  await (w.vm as any).onChange([3, 7]);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual(["[3,7]"]); // serialize
  await (w.vm as any).onChange([]);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([undefined]); // 空→undefined
});

test("parse 处理空/非法输入", () => {
  const w = mount(DimensionMultiSelect);
  const vm = w.vm as any;
  expect(vm.parse(undefined)).toEqual([]);
  expect(vm.parse("")).toEqual([]);
  expect(vm.parse("not-json")).toEqual([]);
  expect(vm.parse("{}")).toEqual([]);
  expect(vm.parse("[1,2]")).toEqual([1, 2]);
});

test("serialize 空数组返回 undefined", () => {
  const w = mount(DimensionMultiSelect);
  const vm = w.vm as any;
  expect(vm.serialize([])).toBeUndefined();
  expect(vm.serialize([3, 7])).toEqual("[3,7]");
});
