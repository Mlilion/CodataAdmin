/**
 * JOIN 条件(on_expr)的结构化生成与回显解析。
 *
 * 后端 join_planner._on_expr_problem 要求:
 * - 顶层 AND 链上的等值条件,两侧限定列前缀(小写规范化后)恰好是两实体的 code;
 * - 禁止注释、子查询、OR/NOT 包裹、第三张表、库限定前缀。
 * 结构化生成固定输出 `leftCode.列 = rightCode.列` 的最干净形态;
 * 回显解析失败(存量手写表达式含 CAST 等)必须返回 null 由调用方落到高级模式,
 * 且绝不改写原文。
 */

export interface JoinCondRow {
  /** 左侧实体的物理列名 */
  left: string;
  /** 右侧实体的物理列名 */
  right: string;
}

const norm = (s: string) => s.trim().toLowerCase();

/** 含注释/引号/括号/分号或这些关键字的表达式不做结构化回显 */
const ADVANCED_PATTERNS = /--|\/\*|\*\/|#|;|'|\(|\)/;
const ADVANCED_KEYWORDS = /\b(OR|IN|EXISTS|SELECT|CASE|CAST|CONVERT|NOT|LIKE|BETWEEN|IS|NULL|COALESCE)\b/i;

export function buildOnExpr(rows: JoinCondRow[], leftCode: string, rightCode: string): string {
  return rows
    .filter((r) => r.left && r.right)
    .map((r) => `${leftCode}.${r.left} = ${rightCode}.${r.right}`)
    .join(" AND ");
}

/**
 * 把 on_expr 解析回结构化行;任何不能安全还原的形态返回 null。
 * 支持登记方向互换(表达式前缀左右与 from/to 相反时逐行交换)。
 */
export function parseOnExpr(
  expr: string | undefined | null,
  leftCode: string,
  rightCode: string
): JoinCondRow[] | null {
  const trimmed = (expr ?? "").trim();
  if (!trimmed) return null;
  if (ADVANCED_PATTERNS.test(trimmed) || ADVANCED_KEYWORDS.test(trimmed)) return null;
  const lc = norm(leftCode);
  const rc = norm(rightCode);
  if (!lc || !rc) return null;

  const rows: JoinCondRow[] = [];
  for (const part of trimmed.split(/\s+AND\s+/i)) {
    const m = part.trim().match(/^(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)$/);
    if (!m) return null;
    const lTable = m[1]!;
    const lCol = m[2]!;
    const rTable = m[3]!;
    const rCol = m[4]!;
    if (norm(lTable) === lc && norm(rTable) === rc) {
      rows.push({ left: lCol, right: rCol });
    } else if (norm(lTable) === rc && norm(rTable) === lc) {
      rows.push({ left: rCol, right: lCol });
    } else {
      return null; // 第三张表或前缀对不上,交回高级模式
    }
  }
  return rows.length ? rows : null;
}
