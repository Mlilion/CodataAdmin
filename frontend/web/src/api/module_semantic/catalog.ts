import { request } from "@utils";

const API_PATH = "/semantic/catalog";

// 慢接口超时(默认 15s 不够): 采集/同步要回源数仓, 连接测试含 30s connect_timeout
const TIMEOUT_SLOW = 10 * 60 * 1000; // 10min
const TIMEOUT_TEST = 45_000;

const CatalogAPI = {
  listDatasource() {
    return request<ApiResponse<CatDatasource[]>>({ url: `${API_PATH}/datasource/list`, method: "get" });
  },

  listDatabase(datasourceId: number) {
    return request<ApiResponse<CatDatabase[]>>({
      url: `${API_PATH}/database/list`,
      method: "get",
      params: { datasource_id: datasourceId },
    });
  },

  listTable(query?: CatTablePageQuery) {
    return request<ApiResponse<PageResult<CatTable>>>({
      url: `${API_PATH}/table/list`,
      method: "get",
      params: query,
    });
  },

  tableDetail(id: number) {
    return request<ApiResponse<CatTableDetail>>({ url: `${API_PATH}/table/detail/${id}`, method: "get" });
  },

  sync(body: SyncRequest) {
    return request<ApiResponse<SyncResult>>({ url: `${API_PATH}/sync`, method: "post", data: body, timeout: TIMEOUT_SLOW });
  },

  // ---- 数据源管理（自采集用，source=manual）----
  createDatasource(body: DatasourceCreateBody) {
    return request<ApiResponse<CatDatasource>>({ url: `${API_PATH}/datasource/create`, method: "post", data: body });
  },

  updateDatasource(id: number, body: DatasourceUpdateBody) {
    return request<ApiResponse<CatDatasource>>({ url: `${API_PATH}/datasource/update/${id}`, method: "put", data: body });
  },

  deleteDatasource(ids: number[]) {
    return request<ApiResponse<null>>({ url: `${API_PATH}/datasource/delete`, method: "delete", data: ids });
  },

  testDatasource(id: number) {
    return request<ApiResponse<boolean>>({ url: `${API_PATH}/datasource/test/${id}`, method: "post", timeout: TIMEOUT_TEST });
  },

  collect(body: CollectRequest) {
    return request<ApiResponse<SyncResult>>({ url: `${API_PATH}/collect`, method: "post", data: body, timeout: TIMEOUT_SLOW });
  },
};

export default CatalogAPI;

export type DatasourceType = "starrocks" | "mysql" | "redshift";

export interface CatDatasource {
  id: number;
  external_id?: string;
  name: string;
  ds_type?: DatasourceType;
  host?: string;
  port?: number;
  username?: string;
  default_database?: string;
  description?: string;
  source?: string;
  synced_at?: string;
}

export interface DatasourceCreateBody {
  name: string;
  ds_type?: DatasourceType;
  host: string;
  port: number;
  username: string;
  password?: string;
  default_database?: string;
  description?: string;
}

export type DatasourceUpdateBody = Partial<DatasourceCreateBody>;

export interface CollectRequest {
  datasource_id: number;
  db_names?: string[];
}

export interface CatDatabase {
  id: number;
  datasource_id: number;
  name: string;
  table_count: number;
  synced_at?: string;
}

export interface CatTablePageQuery extends PageQuery {
  database_id?: number;
  db_name?: string;
  name?: string;
}

export interface CatTable {
  id: number;
  database_id: number;
  db_name: string;
  name: string;
  table_type?: string;
  row_count?: number;
  comment?: string;
  synced_at?: string;
}

export interface CatColumn {
  id: number;
  ordinal: number;
  name: string;
  col_type?: string;
  column_type_full?: string;
  key_type?: string;
  is_nullable: boolean;
  comment?: string;
  description?: string;
}

export interface CatTableDetail extends CatTable {
  data_size?: number;
  description?: string;
  physical_update_time?: string;
  columns: CatColumn[];
}

export interface SyncRequest {
  external_datasource_id: string;
  name?: string;
  db_names?: string[];
}

export interface SyncResult {
  datasource_id: number;
  databases: number;
  tables: number;
  columns: number;
  message: string;
}
