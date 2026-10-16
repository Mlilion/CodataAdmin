import { request } from "@utils";

const API_PATH = "/semantic/querylog";

/** 挖掘出的别名候选（待人工审核） */
export interface AliasCandidate {
  /** 候选别名：归一化后的用户说法 */
  alias: string;
  /** 资产类型 indicator / dimension / ... */
  node_type: string | null;
  /** 归属资产 code。★ 取的是**实际采纳**的那个，不是检索给的 top1 */
  node_code: string;
  /** 被采纳次数 */
  hits: number;
  source: string;
  /** 恒为 true —— 挖掘产物不自动获得权威性，必须人工过一遍 */
  needs_review: boolean;
  /**
   * 贡献该候选的调用方。
   *
   * ★ 复数：候选是按说法聚合的，同一问法可能被多个调用方问过。
   * 有了它才判断得出"这条是巡检脚本刷出来的还是真人反复这么叫" ——
   * 两者的采纳标准不同（脚本里写死的措辞采纳成别名没有价值）。
   */
  callers: string[];
}

/** 零召回的说法：别名缺失最直接的证据 */
export interface ZeroRecallItem {
  query: string;
  hits: number;
  callers: string[];
}

/** 一个调用方及其提问量（筛选框用） */
export interface CallerStat {
  caller: string;
  hits: number;
}

export interface AliasApproveResult {
  ok: boolean;
  node_type: string;
  node_code: string;
  alias: string;
  /** 此前已存在（幂等重复采纳） */
  already_present: boolean;
  total_aliases: number;
}

const QueryLogAPI = {
  /**
   * 别名候选：被反复问且**被明确采纳**的说法。
   *
   * 只统计 adopted=true。未反馈（NULL）既不算正例也不算负例 —— 绝大多数日志
   * 都是 NULL，把它当正例会把检索的错误答案固化成别名。
   */
  aliasCandidates(params?: { since_days?: number; min_hits?: number; limit?: number; caller?: string }) {
    return request<ApiResponse<AliasCandidate[]>>({
      url: `${API_PATH}/alias-candidates`,
      method: "get",
      params,
    });
  },

  /** 零召回清单。比采纳信号更早可用：不需要用户反馈 */
  zeroRecall(params?: { since_days?: number; limit?: number; caller?: string }) {
    return request<ApiResponse<ZeroRecallItem[]>>({
      url: `${API_PATH}/zero-recall`,
      method: "get",
      params,
    });
  },

  /**
   * 有哪些调用方问过东西（筛选框用），按提问量倒序。
   *
   * ★ 不让运营手打 caller 名：打错一个字得到的是空列表，而空列表看起来像
   * "这个调用方没问过东西"，不像"你打错了" —— 归因错误比没功能更糟。
   */
  callers(params?: { since_days?: number }) {
    return request<ApiResponse<CallerStat[]>>({
      url: `${API_PATH}/callers`,
      method: "get",
      params,
    });
  },

  /**
   * 清理超期 query 日志（**软删**，误清可恢复）。
   *
   * ★ 定时任务已每周自动清一次，本入口是"日志表已经涨大、现在就要清"的补救 ——
   * 与 retrieval/rebuild 同时有定时与手动入口是同一逻辑。
   * 权限复用 querylog:approve（写动作）。
   */
  purge(keepDays: number) {
    return request<ApiResponse<{ purged: number; keep_days: number }>>({
      url: `${API_PATH}/purge`,
      method: "post",
      params: { keep_days: keepDays },
    });
  },

  /** 采纳一条候选：并进该资产的 aliases（合并，不覆盖手工补的）。写活表 + 同步副库, 立即生效 */
  approveAlias(data: { alias: string; node_type: string; node_code: string }) {
    return request<ApiResponse<AliasApproveResult>>({
      url: `${API_PATH}/approve-alias`,
      method: "post",
      data,
    });
  },
};

export default QueryLogAPI;
