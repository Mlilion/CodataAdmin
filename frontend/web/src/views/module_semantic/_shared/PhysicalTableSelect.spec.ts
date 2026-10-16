import { mount, flushPromises } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import CatalogAPI from "@/api/module_semantic/catalog";
vi.mock("@/api/module_semantic/catalog", () => ({
  default: {
    listDatasource: vi.fn().mockResolvedValue({ data: { data: [{ id: 1, name: "sr_prod" }] } }),
    listDatabase: vi
      .fn()
      .mockResolvedValue({ data: { data: [{ id: 10, name: "dws", datasource_id: 1 }] } }),
    listTable: vi.fn().mockResolvedValue({
      data: { data: { items: [{ id: 100, name: "dws_order_df", db_name: "dws" }], total: 1 } },
    }),
    tableDetail: vi.fn().mockResolvedValue({
      data: {
        data: {
          id: 100,
          name: "dws_order_df",
          db_name: "dws",
          database_id: 10,
          columns: [
            { id: 201, name: "order_id", ordinal: 1, is_nullable: false },
            { id: 202, name: "order_date", ordinal: 2, is_nullable: true },
          ],
        },
      },
    }),
  },
}));
import PhysicalTableSelect from "./PhysicalTableSelect.vue";

test("选择表后 emit tableId 与 meta", async () => {
  const w = mount(PhysicalTableSelect, { props: { modelValue: undefined } });
  await flushPromises();
  await (w.vm as any).onDatasourceChange(1);
  await flushPromises();
  await (w.vm as any).onDatabaseChange(10);
  await flushPromises();
  await (w.vm as any).onTableChange(100);
  expect(w.emitted("update:modelValue")?.at(-1)).toEqual([100]);
  expect(w.emitted("update:meta")?.at(-1)?.[0]).toMatchObject({
    database: "dws",
    table: "dws_order_df",
  });
});

test("选择表后 emit 该物理表的全部列", async () => {
  const w = mount(PhysicalTableSelect, { props: { modelValue: undefined } });
  await flushPromises();

  await (w.vm as any).onDatasourceChange(1);
  await flushPromises();
  await (w.vm as any).onDatabaseChange(10);
  await flushPromises();
  await (w.vm as any).onTableChange(100);
  await flushPromises();

  expect(w.emitted("update:columns")?.at(-1)).toEqual([
    [
      { id: 201, name: "order_id", ordinal: 1, is_nullable: false },
      { id: 202, name: "order_date", ordinal: 2, is_nullable: true },
    ],
  ]);
});

test("清空物理表时同时清空物理位置元信息", async () => {
  const w = mount(PhysicalTableSelect, { props: { modelValue: undefined } });
  await flushPromises();

  await (w.vm as any).onDatasourceChange(1);
  await flushPromises();
  await (w.vm as any).onDatabaseChange(10);
  await flushPromises();
  await (w.vm as any).onTableChange(100);
  await flushPromises();
  await (w.vm as any).onTableChange(undefined);

  expect(w.emitted("update:meta")?.at(-1)).toEqual([{}]);
});

test("数据源快速切换时旧数据库响应不能覆盖新数据源", async () => {
  vi.clearAllMocks();
  let resolveOld!: (value: unknown) => void;
  const oldDatabases = new Promise((resolve) => {
    resolveOld = resolve;
  });
  vi.mocked(CatalogAPI.listDatabase)
    .mockImplementationOnce(() => oldDatabases as Promise<any>)
    .mockResolvedValueOnce({
      data: {
        code: 200,
        msg: "",
        status_code: 200,
        success: true,
        data: [{ id: 20, name: "ads", datasource_id: 2, table_count: 1 }],
      },
    } as any);

  const w = mount(PhysicalTableSelect, { props: { modelValue: undefined } });
  await flushPromises();
  const vm = w.vm as any;

  const oldSelection = vm.onDatasourceChange(1);
  const newSelection = vm.onDatasourceChange(2);
  await newSelection;
  resolveOld({ data: { data: [{ id: 10, name: "dws", datasource_id: 1, table_count: 1 }] } });
  await oldSelection;
  await flushPromises();

  expect(vm.dsId).toBe(2);
  expect(vm.databases).toEqual([{ id: 20, name: "ads", datasource_id: 2, table_count: 1 }]);
});

test("库快速切换时旧物理表响应不能覆盖新库", async () => {
  vi.clearAllMocks();
  let resolveOld!: (value: unknown) => void;
  const oldTables = new Promise((resolve) => {
    resolveOld = resolve;
  });
  vi.mocked(CatalogAPI.listTable)
    .mockImplementationOnce(() => oldTables as Promise<any>)
    .mockResolvedValueOnce({
      data: {
        data: {
          items: [{ id: 200, name: "ads_customer_df", db_name: "ads", database_id: 20 }],
          total: 1,
          page_no: 1,
          page_size: 50,
          has_next: false,
        },
      },
    } as any);

  const w = mount(PhysicalTableSelect, { props: { modelValue: undefined } });
  await flushPromises();
  const vm = w.vm as any;

  const oldSelection = vm.onDatabaseChange(10);
  const newSelection = vm.onDatabaseChange(20);
  await newSelection;
  resolveOld({
    data: {
      data: {
        items: [{ id: 100, name: "dws_order_df", db_name: "dws", database_id: 10 }],
        total: 1,
      },
    },
  });
  await oldSelection;
  await flushPromises();

  expect(vm.dbId).toBe(20);
  expect(vm.tables).toEqual([
    { id: 200, name: "ads_customer_df", db_name: "ads", database_id: 20 },
  ]);
});

test("物理表快速搜索时旧关键词响应不能覆盖新关键词", async () => {
  vi.clearAllMocks();
  let resolveOld!: (value: unknown) => void;
  const oldTables = new Promise((resolve) => {
    resolveOld = resolve;
  });
  vi.mocked(CatalogAPI.listTable)
    .mockImplementationOnce(() => oldTables as Promise<any>)
    .mockResolvedValueOnce({
      data: {
        data: {
          items: [{ id: 200, name: "dws_customer_df", db_name: "dws", database_id: 10 }],
          total: 1,
          page_no: 1,
          page_size: 50,
          has_next: false,
        },
      },
    } as any);

  const w = mount(PhysicalTableSelect, { props: { modelValue: undefined } });
  await flushPromises();
  const vm = w.vm as any;

  const oldSearch = vm.onDatabaseChange(10);
  const newSearch = vm.searchTables("customer");
  await newSearch;
  resolveOld({
    data: {
      data: {
        items: [{ id: 100, name: "dws_order_df", db_name: "dws", database_id: 10 }],
        total: 1,
        page_no: 1,
        page_size: 50,
        has_next: false,
      },
    },
  });
  await oldSearch;
  await flushPromises();

  expect(vm.tables).toEqual([
    { id: 200, name: "dws_customer_df", db_name: "dws", database_id: 10 },
  ]);
});

test("三级筛选器为物理表保留更宽的选择控件", async () => {
  const w = mount(PhysicalTableSelect, { props: { modelValue: undefined } });

  expect(w.find("[data-role='physical-table-select']").exists()).toBe(true);
  expect(w.find("[data-role='physical-table-select-table']").classes()).toContain(
    "physical-table-select__table"
  );
});

test("编辑态挂载(有初始 modelValue)预载已绑物理表并回显表名/回填 dbId", async () => {
  const w = mount(PhysicalTableSelect, { props: { modelValue: 100 } });
  await flushPromises();
  const vm = w.vm as any;
  // 表名回显: tables 列表含该表
  expect(vm.tables.some((t: any) => t.id === 100 && t.name === "dws_order_df")).toBe(true);
  // 回填到"库"一级
  expect(vm.dbId).toBe(10);
  // 不应误清空已选(不 emit undefined)
  expect(w.emitted("update:modelValue")).toBeUndefined();
});

test("编辑态预载旧表结果不能覆盖用户随后选择的新表列", async () => {
  vi.clearAllMocks();
  let resolveOld!: (value: unknown) => void;
  const oldDetail = new Promise((resolve) => {
    resolveOld = resolve;
  });
  vi.mocked(CatalogAPI.tableDetail)
    .mockImplementationOnce(() => oldDetail as Promise<any>)
    .mockResolvedValueOnce({
      data: {
        code: 200,
        msg: "",
        status_code: 200,
        success: true,
        data: {
          id: 200,
          name: "dws_customer_df",
          db_name: "dws",
          database_id: 10,
          columns: [{ id: 202, name: "customer_id", ordinal: 1, is_nullable: false }],
        },
      },
    } as any);

  const w = mount(PhysicalTableSelect, { props: { modelValue: 100 } });
  await flushPromises();
  const vm = w.vm as any;
  vm.tables.push({ id: 200, name: "dws_customer_df", db_name: "dws", database_id: 10 });

  const newSelection = vm.onTableChange(200);
  await newSelection;
  resolveOld({
    code: 200,
    msg: "",
    status_code: 200,
    success: true,
    data: {
      data: {
        id: 100,
        name: "dws_order_df",
        db_name: "dws",
        database_id: 10,
        columns: [{ id: 201, name: "order_id", ordinal: 1, is_nullable: false }],
      },
    },
  });
  await flushPromises();

  expect(w.emitted("update:columns")?.at(-1)).toEqual([
    [{ id: 202, name: "customer_id", ordinal: 1, is_nullable: false }],
  ]);
});
