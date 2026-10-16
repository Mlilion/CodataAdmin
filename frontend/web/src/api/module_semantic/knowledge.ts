import { request } from "@utils";

const API_PATH = "/semantic/knowledge";

const KnowledgeAPI = {
  listKnowledge(query?: KnowledgePageQuery) {
    return request<ApiResponse<PageResult<KnowledgeTable>>>({ url: `${API_PATH}/list`, method: "get", params: query });
  },

  detailKnowledge(id: number) {
    return request<ApiResponse<KnowledgeTable>>({ url: `${API_PATH}/detail/${id}`, method: "get" });
  },

  createKnowledge(body: KnowledgeForm) {
    return request<ApiResponse<KnowledgeTable>>({ url: `${API_PATH}/create`, method: "post", data: body });
  },

  updateKnowledge(id: number, body: KnowledgeForm) {
    return request<ApiResponse<KnowledgeTable>>({ url: `${API_PATH}/update/${id}`, method: "put", data: body });
  },

  deleteKnowledge(body: number[]) {
    return request<ApiResponse>({ url: `${API_PATH}/delete`, method: "delete", data: body });
  },

  changeStatus(id: number, status: number) {
    return request<ApiResponse<KnowledgeTable>>({ url: `${API_PATH}/status/${id}`, method: "patch", data: { status } });
  },

  /**
   * 待复核清单：已发布但从未复核 / 超过复核周期 / 被级联标记。
   *
   * 与列表页的区别：这三类进清单的条件都算不出来（跨字段 + 时间窗 + 级联标记），
   * 列表页的 status 筛选给不出，所以它必须是独立入口。
   */
  listStale() {
    return request<ApiResponse<KnowledgeTable[]>>({ url: `${API_PATH}/stale`, method: "get" });
  },

  /**
   * 标记已复核：刷新 reviewed_at 并清掉 needs_review。
   *
   * ★ LLM 来源的知识必须先经此动作才能发布（后端源守卫 change_status 会拦）。
   * 此前前端没有这个方法，那道守卫在 UI 上是一道死门 —— 发布报错却无处可解。
   */
  markReviewed(id: number) {
    return request<ApiResponse<KnowledgeTable>>({ url: `${API_PATH}/review/${id}`, method: "put" });
  },

  /**
   * 扫描悬空引用：引用目标已失效的知识会被标记 needs_review。
   *
   * 写入时已校验引用存在，本扫描防的是「知识挂好之后目标被下线」。返回被标记的 id。
   */
  scanOrphanRefs() {
    return request<ApiResponse<number[]>>({ url: `${API_PATH}/scan-orphan-refs`, method: "post" });
  },
};

export default KnowledgeAPI;

export interface KnowledgePageQuery extends PageQuery {
  key?: string;
  summary?: string;
  knowledge_type?: string;
  usage_mode?: string;
  status?: number;
}

export interface KnowledgeRef {
  id?: number;
  knowledge_id?: number;
  target_type: string;
  target_id: number;
  target_label?: string;
  ref_type: string;
}

export interface KnowledgeTable extends BaseType {
  key: string;
  summary: string;
  body?: string;
  knowledge_type: string;
  representative_sql?: string;
  tags?: string;
  usage_mode: string;
  source: string;
  confidence?: number;
  status: number;
  owner_id?: number;
  reviewed_at?: string;
  review_interval_days: number;
  needs_review: boolean;
  /** 口径分水岭：最后一个旧口径日，次日起为新口径。驱动校验器 caliber_break 规则 */
  caliber_break_date?: string | null;
  /** 口径差异一句话说明，会进拒答消息给 agent */
  caliber_break_note?: string | null;
  refs: KnowledgeRef[];
}

export interface KnowledgeForm extends BaseFormType {
  key: string;
  summary: string;
  body?: string;
  knowledge_type: string;
  representative_sql?: string;
  tags?: string;
  usage_mode: string;
  source?: string;
  confidence?: number;
  owner_id?: number;
  reviewed_at?: string;
  review_interval_days?: number;
  needs_review?: boolean;
  caliber_break_date?: string | null;
  caliber_break_note?: string | null;
  refs?: KnowledgeRef[];
}
