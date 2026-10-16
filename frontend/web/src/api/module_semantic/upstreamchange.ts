import { request } from "@utils";

const API_PATH = "/semantic/catalog/change";

/** 影响级别：处理动作不同，故三级而非布尔 */
export type ImpactLevel = "bound" | "sql_ref" | "table_only";
export type ChangeStatus = "pending" | "resolved" | "ignored";

/** 受影响的语义资产清单 */
export interface ImpactDetail {
  fields?: { entity: string; field: string; id: number }[];
  entities?: { entity: string; id: number }[];
  impls?: { indicator: string; role: string; id: number }[];
}

export interface UpstreamChange {
  id: number;
  change_kind: "column_added" | "column_removed" | "column_type_changed" | "table_removed";
  db_name: string;
  table_name: string;
  column_name?: string | null;
  before_value?: string | null;
  after_value?: string | null;
  impact_level: ImpactLevel;
  /** 后端已解析成对象，前端不必 JSON.parse */
  impact_detail: ImpactDetail;
  status: ChangeStatus;
  resolve_note?: string | null;
  resolved_at?: string | null;
  detected_at?: string | null;
  last_seen_at?: string | null;
}

const UpstreamChangeAPI = {
  /** 默认只回 pending —— 待办箱打开就该是"待我处理的" */
  listChange(params?: { status?: ChangeStatus; impact_level?: ImpactLevel }) {
    return request<ApiResponse<UpstreamChange[]>>({
      url: `${API_PATH}/list`,
      method: "get",
      params,
    });
  },

  resolveChange(id: number, resolveNote?: string) {
    return request<ApiResponse<UpstreamChange>>({
      url: `${API_PATH}/resolve/${id}`,
      method: "patch",
      data: { resolve_note: resolveNote },
    });
  },

  /** ★ 忽略必须写原因 —— 后端会拒绝空原因 */
  ignoreChange(id: number, resolveNote: string) {
    return request<ApiResponse<UpstreamChange>>({
      url: `${API_PATH}/ignore/${id}`,
      method: "patch",
      data: { resolve_note: resolveNote },
    });
  },
};

export default UpstreamChangeAPI;
