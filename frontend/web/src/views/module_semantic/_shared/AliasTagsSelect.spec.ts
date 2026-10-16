import { mount } from "@vue/test-utils";
import { expect, test } from "vitest";
import AliasTagsSelect from "./AliasTagsSelect.vue";

test("回显 JSON 数组字符串并 emit JSON 串", () => {
  const w = mount(AliasTagsSelect, { props: { modelValue: '["GMV"]' } });
  expect((w.vm as any).selected).toEqual(["GMV"]);
  (w.vm as any).onChange(["GMV", "成交额"]);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual(['["GMV","成交额"]']);
});

test("清空 emit undefined(键从 payload 消失)", () => {
  const w = mount(AliasTagsSelect, { props: { modelValue: '["GMV"]' } });
  (w.vm as any).onChange([]);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([undefined]);
});

test("脏值按空处理且挂载时不主动 emit(不改写存量)", () => {
  const w = mount(AliasTagsSelect, { props: { modelValue: "not-json" } });
  expect((w.vm as any).selected).toEqual([]);
  expect(w.emitted("update:modelValue")).toBeUndefined();
});

test("parse 处理空/非法/非数组输入", () => {
  const vm = mount(AliasTagsSelect).vm as any;
  expect(vm.parse(undefined)).toEqual([]);
  expect(vm.parse("")).toEqual([]);
  expect(vm.parse("not-json")).toEqual([]);
  expect(vm.parse("{}")).toEqual([]);
  expect(vm.parse('["a",""]')).toEqual(["a"]);
});
