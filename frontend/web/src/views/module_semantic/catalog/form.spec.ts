import { describe, expect, it } from "vitest";

import {
  buildDatasourceBody,
  createDatasourceForm,
  createDatasourceFormFromRow,
  defaultDatasourcePort,
  formatDatasourceType,
  normalizeDatasourceType,
} from "./form";

describe("catalog datasource form helpers", () => {
  it("creates the default starrocks form", () => {
    expect(createDatasourceForm()).toEqual({
      name: "",
      ds_type: "starrocks",
      host: "",
      port: 9030,
      username: "",
      password: "",
      default_database: "",
      description: "",
    });
  });

  it("normalizes and formats datasource types", () => {
    expect(normalizeDatasourceType(" Redshift ")).toBe("redshift");
    expect(defaultDatasourcePort("redshift")).toBe(5439);
    expect(defaultDatasourcePort("mysql")).toBe(3306);
    expect(defaultDatasourcePort("unknown")).toBe(9030);
    expect(formatDatasourceType("mysql")).toBe("MySQL");
  });

  it("maps datasource rows into forms and trims the submit body", () => {
    const form = createDatasourceFormFromRow({
      id: 7,
      name: "  Warehouse  ",
      ds_type: "redshift",
      host: " redshift.example ",
      port: 5439,
      username: " readonly ",
      default_database: " dev ",
      description: " semantic ",
    });

    expect(form).toEqual({
      id: 7,
      name: "Warehouse",
      ds_type: "redshift",
      host: "redshift.example",
      port: 5439,
      username: "readonly",
      password: "",
      default_database: "dev",
      description: "semantic",
    });
    expect(buildDatasourceBody(form)).toEqual({
      name: "Warehouse",
      ds_type: "redshift",
      host: "redshift.example",
      port: 5439,
      username: "readonly",
      password: undefined,
      default_database: "dev",
      description: "semantic",
    });
  });
});
