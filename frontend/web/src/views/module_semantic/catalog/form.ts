import type { CatDatasource, DatasourceCreateBody, DatasourceType } from "@/api/module_semantic/catalog";

export interface DatasourceForm {
  id?: number;
  name: string;
  ds_type: DatasourceType;
  host: string;
  port: number;
  username: string;
  password: string;
  default_database: string;
  description: string;
}

export const DATASOURCE_TYPE_OPTIONS = [
  { label: "StarRocks", value: "starrocks" as const },
  { label: "MySQL", value: "mysql" as const },
  { label: "Redshift", value: "redshift" as const },
] as const;

const DATASOURCE_TYPE_LABELS: Record<DatasourceType, string> = {
  starrocks: "StarRocks",
  mysql: "MySQL",
  redshift: "Redshift",
};

export function normalizeDatasourceType(value?: string | null): DatasourceType {
  const normalized = (value || "").trim().toLowerCase();
  if (normalized === "mysql" || normalized === "redshift") return normalized;
  return "starrocks";
}

export function defaultDatasourcePort(value?: string | null): number {
  const datasourceType = normalizeDatasourceType(value);
  if (datasourceType === "mysql") return 3306;
  if (datasourceType === "redshift") return 5439;
  return 9030;
}

export function createDatasourceForm(): DatasourceForm {
  return {
    name: "",
    ds_type: "starrocks",
    host: "",
    port: 9030,
    username: "",
    password: "",
    default_database: "",
    description: "",
  };
}

export function createDatasourceFormFromRow(row?: CatDatasource): DatasourceForm {
  if (!row) return createDatasourceForm();
  const dsType = normalizeDatasourceType(row.ds_type);
  return {
    id: row.id,
    name: row.name?.trim() || "",
    ds_type: dsType,
    host: row.host?.trim() || "",
    port: row.port || defaultDatasourcePort(dsType),
    username: row.username?.trim() || "",
    password: "",
    default_database: row.default_database?.trim() || "",
    description: row.description?.trim() || "",
  };
}

export function buildDatasourceBody(form: DatasourceForm): DatasourceCreateBody {
  return {
    name: form.name.trim(),
    ds_type: normalizeDatasourceType(form.ds_type),
    host: form.host.trim(),
    port: form.port,
    username: form.username.trim(),
    password: form.password.trim() || undefined,
    default_database: form.default_database.trim() || undefined,
    description: form.description.trim() || undefined,
  };
}

export function formatDatasourceType(value?: string | null): string {
  const normalized = normalizeDatasourceType(value);
  return DATASOURCE_TYPE_LABELS[normalized] || value?.trim() || "-";
}
