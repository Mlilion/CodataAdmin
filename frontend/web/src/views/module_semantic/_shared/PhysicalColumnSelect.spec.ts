import { mount, flushPromises } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import CatalogAPI from "@/api/module_semantic/catalog";
vi.mock("@/api/module_semantic/catalog", () => ({
  default: {
    tableDetail: vi.fn().mockResolvedValue({
      data: { data: { id: 100, columns: [{ id: 5, name: "pay_amount", ordinal: 1 }] } },
    }),
  },
}));
import PhysicalColumnSelect from "./PhysicalColumnSelect.vue";

test("给定 tableId 加载列并 emit columnId+meta", async () => {
  const w = mount(PhysicalColumnSelect, { props: { tableId: 100, modelValue: undefined } });
  await flushPromises();
  await (w.vm as any).onColumnChange(5);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([5]);
  expect(w.emitted("update:meta")?.at(-1)?.[0]).toMatchObject({ column: "pay_amount" });
});

test("meta 附带列类型与注释,供父表单回填 data_type/名称", async () => {
  const w = mount(PhysicalColumnSelect, {
    props: {
      tableId: 100,
      modelValue: undefined,
      columns: [
        {
          id: 9,
          ordinal: 1,
          name: "pay_amount",
          col_type: "decimal(18,2)",
          comment: "支付金额",
          is_nullable: false,
        },
      ],
    },
  });
  await flushPromises();
  await (w.vm as any).onColumnChange(9);
  expect(w.emitted("update:meta")?.at(-1)?.[0]).toMatchObject({
    column: "pay_amount",
    colType: "decimal(18,2)",
    comment: "支付金额",
  });
});

test("编辑态挂载(tableId+modelValue 均有初值)不应 emit update:modelValue(undefined)", async () => {
  const w = mount(PhysicalColumnSelect, { props: { tableId: 100, modelValue: 5 } });
  await flushPromises();
  expect(w.emitted("update:modelValue")).toBeUndefined();
});

test("挂载后 tableId 从 100 变到 200 时清空已选(emit undefined)", async () => {
  const w = mount(PhysicalColumnSelect, { props: { tableId: 100, modelValue: 5 } });
  await flushPromises();
  await w.setProps({ tableId: 200 });
  await flushPromises();
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([undefined]);
  expect(w.emitted("update:meta")?.at(-1)).toEqual([{}]);
});

test("父组件提供物理列清单时优先使用清单而不重复请求表详情", async () => {
  vi.clearAllMocks();
  const w = mount(PhysicalColumnSelect, {
    props: {
      tableId: 100,
      modelValue: undefined,
      columns: [{ id: 6, name: "customer_id", ordinal: 1, is_nullable: false }],
    },
  });
  await flushPromises();

  await (w.vm as any).onColumnChange(6);

  expect(CatalogAPI.tableDetail).not.toHaveBeenCalled();
  expect(w.emitted("update:meta")?.at(-1)?.[0]).toMatchObject({ column: "customer_id" });
});

test("父组件列清单为空时保留按表详情加载列的兜底", async () => {
  vi.clearAllMocks();
  const w = mount(PhysicalColumnSelect, {
    props: {
      tableId: 100,
      modelValue: undefined,
      columns: [],
    },
  });
  await flushPromises();

  await (w.vm as any).onColumnChange(5);

  expect(CatalogAPI.tableDetail).toHaveBeenCalledWith(100);
  expect(w.emitted("update:meta")?.at(-1)?.[0]).toMatchObject({ column: "pay_amount" });
});

test("按表详情兜底加载时旧表响应不能覆盖新表列", async () => {
  vi.clearAllMocks();
  let resolveOld!: (value: unknown) => void;
  const oldDetail = new Promise((resolve) => {
    resolveOld = resolve;
  });
  vi.mocked(CatalogAPI.tableDetail)
    .mockImplementationOnce(() => oldDetail as Promise<any>)
    .mockResolvedValueOnce({
      data: {
        data: {
          id: 200,
          name: "dws_customer_df",
          db_name: "dws",
          database_id: 10,
          columns: [{ id: 6, name: "customer_id", ordinal: 1, is_nullable: false }],
        },
      },
    } as any);

  const w = mount(PhysicalColumnSelect, { props: { tableId: 100, modelValue: undefined } });
  await w.setProps({ tableId: 200 });
  await flushPromises();
  resolveOld({
    data: {
      data: {
        id: 100,
        columns: [{ id: 5, name: "pay_amount", ordinal: 1, is_nullable: false }],
      },
    },
  });
  await flushPromises();

  await (w.vm as any).onColumnChange(6);

  expect(w.emitted("update:meta")?.at(-1)?.[0]).toMatchObject({ column: "customer_id" });
});
