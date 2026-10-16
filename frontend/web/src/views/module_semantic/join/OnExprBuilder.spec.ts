import { mount, flushPromises } from "@vue/test-utils";
import { expect, test, vi } from "vitest";

vi.mock("@/api/module_semantic/entity", () => ({
  default: {
    detailEntity: vi.fn((id: number) =>
      Promise.resolve({
        data: {
          data:
            id === 1
              ? {
                  id: 1,
                  code: "order_revenue",
                  name: "订单收入",
                  fields: [
                    { code: "pay_amt", name: "支付金额", physical_column: "final_revenue", field_role: "measure", visibility: "public" },
                    { code: "uid", name: "用户", physical_column: "user_id", field_role: "id", visibility: "public" },
                    { code: "no_phys", name: "未绑物理列", field_role: "dimension", visibility: "public" },
                  ],
                }
              : {
                  id: 2,
                  code: "dim_user",
                  name: "用户维表",
                  fields: [
                    { code: "id", name: "主键", physical_column: "user_id", field_role: "id", visibility: "public" },
                  ],
                },
        },
      })
    ),
  },
}));
import OnExprBuilder from "./OnExprBuilder.vue";

test("实体就绪后从字段下拉生成规范表达式", async () => {
  const w = mount(OnExprBuilder, {
    props: { fromEntityId: 1, toEntityId: 2, modelValue: undefined },
  });
  await flushPromises();
  const vm = w.vm as any;
  expect(vm.mode).toBe("struct");
  expect(vm.codesReady).toBe(true);
  vm.rows[0].left = "user_id";
  vm.rows[0].right = "id";
  vm.onRowEdit();
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual(["order_revenue.user_id = dim_user.id"]);
});

test("字段选项值为物理列名,未登记物理列回退 code", async () => {
  const w = mount(OnExprBuilder, { props: { fromEntityId: 1, toEntityId: 2 } });
  await flushPromises();
  const vm = w.vm as any;
  expect((vm.leftFieldOptions as { value: string }[]).map((o) => o.value)).toEqual([
    "final_revenue",
    "user_id",
    "no_phys",
  ]);
});

test("编辑回显:合法表达式解析为结构化行且零 emit(不改写原文)", async () => {
  const w = mount(OnExprBuilder, {
    props: {
      fromEntityId: 1,
      toEntityId: 2,
      modelValue: "order_revenue.final_revenue = dim_user.user_id",
    },
  });
  await flushPromises();
  const vm = w.vm as any;
  expect(vm.mode).toBe("struct");
  expect(vm.rows).toEqual([{ left: "final_revenue", right: "user_id" }]);
  expect(w.emitted("update:modelValue")).toBeUndefined();
});

test("不可还原的表达式静默落高级模式且零 emit", async () => {
  const w = mount(OnExprBuilder, {
    props: {
      fromEntityId: 1,
      toEntityId: 2,
      modelValue: "CAST(order_revenue.order_date AS CHAR) = dim_user.date",
    },
  });
  await flushPromises();
  expect((w.vm as any).mode).toBe("advanced");
  expect(w.emitted("update:modelValue")).toBeUndefined();
});

test("高级模式「尝试切回结构化」:无法解析时保持高级模式", async () => {
  const w = mount(OnExprBuilder, {
    props: { fromEntityId: 1, toEntityId: 2, modelValue: "a.b = c.d" },
  });
  await flushPromises();
  const vm = w.vm as any;
  expect(vm.mode).toBe("advanced");
  vm.trySwitchToStruct();
  expect(vm.mode).toBe("advanced");
});

test("多条件结构化生成顶层 AND", async () => {
  const w = mount(OnExprBuilder, {
    props: { fromEntityId: 1, toEntityId: 2, modelValue: undefined },
  });
  await flushPromises();
  const vm = w.vm as any;
  vm.addRow();
  vm.rows[0].left = "user_id";
  vm.rows[0].right = "id";
  vm.rows[1].left = "final_revenue";
  vm.rows[1].right = "user_id";
  vm.onRowEdit();
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([
    "order_revenue.user_id = dim_user.id AND order_revenue.final_revenue = dim_user.user_id",
  ]);
});
