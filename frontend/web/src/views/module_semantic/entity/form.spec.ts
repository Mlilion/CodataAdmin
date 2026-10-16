import { expect, test } from "vitest";
import type { CatColumn } from "@/api/module_semantic/catalog";
import type { SemField } from "@/api/module_semantic/entity";
import { buildEntityFieldsFromColumns, mergeMissingFieldsFromColumns } from "./form";

test("物理列清单映射为默认业务字段并保留物理列绑定", () => {
  const columns: CatColumn[] = [
    { id: 201, name: "order_id", ordinal: 1, is_nullable: false, col_type: "bigint" },
    { id: 202, name: "order_date", ordinal: 2, is_nullable: true, col_type: "date" },
  ];

  expect(buildEntityFieldsFromColumns(columns)).toEqual([
    {
      code: "order_id",
      name: "order_id",
      physical_column: "order_id",
      physical_column_id: 201,
      field_role: "dimension",
      visibility: "public",
    },
    {
      code: "order_date",
      name: "order_date",
      physical_column: "order_date",
      physical_column_id: 202,
      field_role: "dimension",
      visibility: "public",
    },
  ]);
});

test("没有物理列时默认业务字段为空", () => {
  expect(buildEntityFieldsFromColumns([])).toEqual([]);
});

const columns: CatColumn[] = [
  { id: 201, name: "order_id", ordinal: 1, is_nullable: false, col_type: "bigint" },
  { id: 202, name: "order_date", ordinal: 2, is_nullable: true, col_type: "date" },
  { id: 203, name: "amount", ordinal: 3, is_nullable: true, col_type: "decimal" },
];

test("全部列已登记时不追加", () => {
  const existing: SemField[] = [
    { code: "order_id", name: "订单ID", physical_column_id: 201, physical_column: "order_id", field_role: "id", visibility: "public" },
    { code: "dt", name: "日期", physical_column_id: 202, physical_column: "order_date", field_role: "time", visibility: "public" },
    { code: "amount", name: "金额", physical_column_id: 203, physical_column: "amount", field_role: "measure", visibility: "public" },
  ];
  const r = mergeMissingFieldsFromColumns(existing, columns);
  expect(r.added).toBe(0);
  expect(r.fields).toEqual(existing);
});

test("缺失列按 buildEntityFieldsFromColumns 形态追加且不动已有字段", () => {
  const existing: SemField[] = [
    { code: "order_id", name: "订单ID", physical_column_id: 201, physical_column: "order_id", field_role: "id", visibility: "public" },
  ];
  const r = mergeMissingFieldsFromColumns(existing, columns);
  expect(r.added).toBe(2);
  expect(r.fields[0]).toEqual(existing[0]);
  expect(r.fields.slice(1)).toEqual(buildEntityFieldsFromColumns(columns.slice(1, 3)));
});

test("旧数据无 physical_column_id 时按列名回退匹配", () => {
  const existing: SemField[] = [
    { code: "order_date", name: "日期", field_role: "time", visibility: "public" }, // code 恰为列名
    { code: "amt", name: "金额", physical_column: "amount", field_role: "measure", visibility: "public" },
  ];
  const r = mergeMissingFieldsFromColumns(existing, columns);
  expect(r.added).toBe(1);
  expect(r.fields.at(-1)?.code).toBe("order_id");
});
