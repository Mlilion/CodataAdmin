import { request } from "@utils";

const API_PATH = "/semantic/retrieval";

/** 检索副库状态（AI 配置页展示索引健康度用） */
export interface RetrievalStatus {
  enabled: boolean;
  reachable: boolean;
  node_count: number;
  alias_count: number;
  /** 待生成向量的记录数 —— 维度变更后这个值会跳到全量 */
  pending_embedding: number;
  /** pgvector 列的实际宽度。副库不可达时为 null */
  vector_dim: number | null;
  /** 列宽与嵌入配置的维度不一致 —— 此状态下补齐向量会全部失败 */
  dim_mismatch: boolean;
  /** 检索表是否已建。与 reachable 分开：连上了但没建表是另一回事（跑 init-retrieval） */
  schema_ready: boolean;
  fts_enabled: boolean;
  /** 查询期向量路开关（env 部署级熔断） */
  vector_enabled_env: boolean;
  /** 嵌入是否启用（AI 配置，前端可管） */
  vector_enabled_config: boolean;
  /** 两者与端点配置都满足时才为 true */
  vector_effective: boolean;
  /** 未生效的人话原因 —— 只报一个布尔值时运维无从判断该改哪个开关 */
  vector_inactive_reason: string;
  /** = vector_effective（兼容别名） */
  vector_enabled: boolean;
}

/** 语义平台就绪度：blockers 为空即可用 */
export interface SemanticReadiness {
  ready: boolean;
  /** 给运维的行动清单，逐条说明该做什么 */
  blockers: string[];
  retrieval: RetrievalStatus;
  models: {
    released_count: number;
    models: { id: number; code: string; name: string; version: number }[];
  };
  /** 索引版本 vs 已发布版本；stale=true 表示该重建索引 */
  index_freshness: { stale: boolean; indexed: Record<string, number>; released: Record<string, number> };
  /** 数仓只读连接探针（取数能力的前提） */
  warehouse: { configured: boolean; reachable: boolean; latency_ms?: number; error: string | null };
  mcp: { enabled: boolean; authenticated: boolean; mounted: boolean };
}

/** 索引重建结果（后端 RebuildOutSchema） */
export interface RebuildResult {
  /** 处理的模型数 */
  models: number;
  inserted: number;
  updated: number;
  /** 清理的陈旧记录数 */
  pruned: number;
  /** 因总开关关闭而跳过 —— 此时各计数均为 0，不能报成"重建完成" */
  skipped: boolean;
}

const RetrievalAPI = {
  status() {
    return request<ApiResponse<RetrievalStatus>>({
      url: `${API_PATH}/status`,
      method: "get",
    });
  },

  /** 就绪自检：回答"这套东西现在能用吗"。blockers 为空即就绪 */
  readiness() {
    return request<ApiResponse<SemanticReadiness>>({
      url: `${API_PATH}/readiness`,
      method: "get",
    });
  },

  /** 补齐缺向量的记录（增量：判据是 embedding IS NULL） */
  embed() {
    return request<ApiResponse<{ updated: number; failed: number; skipped?: boolean; error?: string }>>({
      url: `${API_PATH}/embed`,
      method: "post",
    });
  },

  /**
   * 重建全部已发布模型的检索索引（幂等）。
   *
   * readiness 的 blocker「模型已发布 vN，索引仍是 vM，请重建检索索引」指向的就是这个动作 ——
   * 在此之前它只有后端端点和权限点，没有任何调用方，运维被提示去做一件 UI 上做不到的事。
   */
  rebuild() {
    return request<ApiResponse<RebuildResult>>({
      url: `${API_PATH}/rebuild`,
      method: "post",
    });
  },
};

export default RetrievalAPI;
