import type { CatColumn } from "@/api/module_semantic/catalog";
import type { SemField } from "@/api/module_semantic/entity";

export function buildEntityFieldsFromColumns(columns: CatColumn[]): SemField[] {
  return columns.map((column) => ({
    code: column.name,
    name: column.name,
    physical_column: column.name,
    physical_column_id: column.id,
    field_role: "dimension",
    visibility: "public",
  }));
}

/**
 * 编辑态「从物理表同步字段」:把列清单中尚未登记到 fields 的列追加进来。
 * 匹配规则:列 id 命中 physical_column_id,或列名命中 physical_column(空回退 code),
 * 兼容旧数据没有 physical_column_id 的情况。命中列不动,只追加缺失列。
 */
export function mergeMissingFieldsFromColumns(
  existing: SemField[],
  columns: CatColumn[]
): { fields: SemField[]; added: number } {
  const boundIds = new Set(
    existing.map((f) => f.physical_column_id).filter((id): id is number => id != null)
  );
  const boundNames = new Set(existing.map((f) => f.physical_column || f.code));
  const missing = columns.filter((c) => !boundIds.has(c.id) && !boundNames.has(c.name));
  return {
    fields: [...existing, ...buildEntityFieldsFromColumns(missing)],
    added: missing.length,
  };
}
