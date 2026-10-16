/**
 * 同义词治理页的纯逻辑（可单测，不依赖 Element Plus 与组件挂载）。
 *
 * 抽出来的理由：页面本身是薄视图，值得测的是这几条**领域规则** ——
 * 它们错了会让治理动作产生错误的检索行为，而这在 UI 上看不出来。
 */

/** 候选的稳定身份：同一别名可挂不同资产，三元组才唯一 */
export interface CandidateKeyed {
  alias: string;
  node_type: string | null;
  node_code: string;
}

/**
 * 候选行的唯一 key。
 *
 * ★ 必须三元组：只用 alias 会让「人均收入→arpu」与「人均收入→arppu」互相覆盖，
 * 采纳一个就把另一个从列表里抹掉，而它们是两条独立待审记录。
 */
export function candidateKey(row: CandidateKeyed): string {
  return `${row.node_type ?? ""}:${row.node_code}:${row.alias}`;
}

/** 采纳成功后把该行移出列表（避免重复点），其余行不受影响。 */
export function removeCandidate<T extends CandidateKeyed>(list: T[], target: CandidateKeyed): T[] {
  const key = candidateKey(target);
  return list.filter(item => candidateKey(item) !== key);
}

/**
 * 采纳时提交的 node_type。
 *
 * 后端只接受 indicator / dimension；挖掘结果理论上总带 node_type，但它来自
 * 数据库聚合，可能为 null。回退到 indicator 而非提交 null —— 后端会因
 * "不支持的资产类型"报错，用户看到的是无从下手的失败。
 */
export function resolveNodeType(nodeType: string | null): string {
  return nodeType === "dimension" ? "dimension" : "indicator";
}

/** 采纳结果的提示文案：区分"新增"与"此前已存在"，别让幂等看起来像没生效。 */
export function approveMessage(alreadyPresent: boolean): string {
  return alreadyPresent ? "该别名此前已存在" : "采纳成功，已生效";
}

/**
 * 筛选用的 caller 参数：空选择 → `undefined`（不带该查询参数）。
 *
 * 后端当前用 `if caller` 判断是否加过滤，空串同样会被当成"全部"，所以这层转换
 * **不是**在绕开一个 bug。它防的是两件事：
 * 1. `caller=` 这种空参数会进请求 URL，也会被 OperationLog 记下来 ——
 *    日后排查"这次查询筛了什么"时，空串与未筛选看起来是两回事。
 * 2. 后端若哪天改成 `if caller is not None`（一个很自然的收紧），空串就会变成
 *    "筛选名为空的调用方" → 返回空列表，而空列表看起来像"这段时间没有候选"，
 *    不像"筛选条件不对"。归因错误比没有功能更糟，故在此显式收口。
 */
export function callerParam(caller: string | null | undefined): string | undefined {
  const trimmed = (caller ?? "").trim();
  return trimmed || undefined;
}
