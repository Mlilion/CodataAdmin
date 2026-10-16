import { request } from "@utils";

const API_PATH = "/semantic/rawsqllog";

/**
 * 一条裸 SQL 调用审计。
 *
 * ★ 含 SQL 原文 —— 后端用独立权限点 `module_semantic:rawsqllog:list` 把关，
 * 因为"能看审计"等于"能看到别人的查询意图与表结构"。
 */
export interface RawSqlLogItem {
  id: number;
  /** 调用方(MCP token 标识 / rest) */
  caller: string | null;
  /** agent 提交的 SQL 原文 */
  raw_sql: string;
  /** 实际执行的(含自动补的 LIMIT)；被守卫拒绝时为空 */
  executed_sql: string | null;
  /** ★ 涉及的表 —— 回答「谁在读哪张表」，这条路最需要被看见的信息 */
  tables: string[];
  ok: boolean;
  /** permission_denied / syntax_error / execution_failed / not_configured */
  error_type: string | null;
  error: string | null;
  row_count: number | null;
  truncated: boolean;
  duration_ms: number | null;
  logged_at: string;
}

export interface RawSqlLogQuery {
  since_days?: number;
  caller?: string;
  /** 只看失败的 —— 排查滥用时最常用的一屏 */
  only_failed?: boolean;
  limit?: number;
}

export const RawSqlLogAPI = {
  /**
   * 最近的裸 SQL 调用，倒序。
   *
   * ★ 返回类型必须是 `ApiResponse<RawSqlLogItem[]>` 而不是裸 `RawSqlLogItem[]`：
   * 后端统一用 `SuccessResponse` 包一层 `{code, msg, data, ...}`，写成裸数组时
   * `res.data` 在**类型上**是数组、**运行时**是那个信封对象 —— typecheck 全绿，
   * 表格却永远空着（且看起来像"这段时间没人用过这条路"，不像"前端解错了"）。
   */
  recent(params: RawSqlLogQuery) {
    return request<ApiResponse<RawSqlLogItem[]>>({
      url: `${API_PATH}/recent`,
      method: "get",
      params,
    });
  },
};
