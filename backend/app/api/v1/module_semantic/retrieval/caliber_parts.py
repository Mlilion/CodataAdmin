"""口径条件反解：把权威 SQL 的 WHERE 拆成「口径条件」与「时间窗条件」。

## 为什么必须有这一步

`sub_revenue` 与 `flux_revenue` 的 `measure_expression` **完全相同**
（`SUM(final_revenue)`），区别只在 `WHERE order_type = 'subscription'` vs `'flux'`。
该条件目前**只存在于 `impl.sql_text` 文本里**，没有结构化字段承载它。

编译两表 SQL 时若不把它搬过去，订阅收入会变成全部收入 —— 而数字照样返回、
SQL 也没错。这正是设计文档里说的"最易出错的一处"。

## 失败一律抛，绝不返回空

返回空列表会被上游读成"这个口径本来就没有额外条件"，于是静默丢掉过滤。
宁可拒绝编译（该指标退回填参路），也不能出一个少了条件的 SQL。

## 判据：含时间占位符的谓词即时间窗

`{start}` / `{end}` / `{as_of}` 是 `sql_binder` 的占位符约定（见 `sql_binder.py`
的 `_PLACEHOLDER`）。含其一的谓词属于时间窗，由编译器按请求的时间参数重新生成；
其余谓词是口径本身，必须原样保留。

## 「拆不干净就抛」的四道闸门（自审补，均实测复现过）

只按顶层 AND 展平、只认 `parse_one` 找到的第一个 WHERE，会在下面四种形态下**返回一个
看起来合法、但口径条件已经不见了的结果**。它们不抛就等于出错数，所以一律抛：

1. **嵌套 WHERE**（子查询/CTE 里也有 WHERE）。`find(exp.Where)` 是深度优先的：外层没有
   WHERE 时它会把**子查询的** WHERE 当成外层口径（搬错层），外层有 WHERE 时子查询的口径
   直接消失。dev 真实指标 `refund_rate`（两个标量子查询相除）实测命中后者 —— 反解出的
   `is_refunded = 1` 只属于分子，搬到顶层会把分母也过滤掉。
2. **多语句**。`parse_one` 只保留第一条、静默丢弃其余（与 `agg_shape._parse` 同一个坑）。
   最险的是丢完之后返回值恰好是 `([], [])` —— 与「本来就没有 WHERE」这个**合法**返回值
   完全相同，上游无从区分。
3. **时间与口径裹在同一个谓词里**（如 `(d BETWEEN '{start}' AND '{end}' AND x = 1)`）。
   括号让它逃过顶层 AND 展平，而时间窗谓词会被编译器**整条重新生成**，`x = 1` 随之丢失。
4. **`OR` 跨越时间与口径**（如 `d >= '{start}' OR x = 1`）。整条当时间窗会丢口径，当口径
   会漏掉时间过滤 —— 两个方向都错，只能拒绝。

抛的代价只是该指标退回填参路（少一次跨实体下钻）；不抛的代价是一个数字对不上的报表。

## 再补两道闸门（复审补，同样实测复现过）

上面四道只看 WHERE，于是漏掉了两种「条件长在 WHERE 之外」的形态。它们的危险性与前四道
完全同源 —— 返回值**非空且形态正常**，上游看不出少了东西：

5. **`HAVING`**。实测
   `split_where("SELECT k,SUM(a) FROM dws.t WHERE d>='{start}' GROUP BY k HAVING SUM(a)>1 AND k<>'x'")`
   → `([], ["d >= '{start}'"])`：`k <> 'x'` 凭空消失。HAVING 作用在聚合**之后**，两类都
   放不进去 —— 归口径会被搬进 WHERE（作用在聚合前，语义变了），归时间窗会被编译器整条
   重新生成（条件直接丢）。dev 现有 21 条口径里没有 HAVING，眼下不可触发，但闸门必须落在
   拆分发生的地方，而不是指望某个消费方记得检查。
6. **顶层集合运算**（UNION / UNION ALL / INTERSECT / EXCEPT）。实测
   `split_where("SELECT a FROM dws.t UNION ALL SELECT b FROM dws.t")` → `([], [])`，
   `source_table(...)` → `'dws.t'`：两个函数都报「成功」，于是下游的两表 JOIN 改写会
   对一段根本不是单层单表 SELECT 的 SQL 开绿灯。而且旧行为自相矛盾 —— 同一段 SQL 只要
   任一分支带上 WHERE 就会抛，判定翻转在一个无关细节上。这一道放在共享的 `_parse` 里，
   两个入口一起堵住。
"""

import sqlglot
from sqlglot import exp

from .dialect import sqlglot_dialect

# 与 sql_binder 的占位符约定一致
_TIME_PLACEHOLDERS = ("{start}", "{end}", "{as_of}")


class CaliberParseError(ValueError):
    """反解失败。抛而不是返回空 —— 空会让上游静默丢掉口径条件。"""


def _parse(sql_text: str) -> exp.Expression:
    """解析成唯一一棵语法树。多语句、顶层集合运算一律抛（闸门 2 / 6）。

    ★ 用 `parse` 而不是 `parse_one`：`parse_one` 只保留第一条语句、静默丢弃其余，
    于是后续语句里的口径条件会凭空消失，而返回值 `([], [])` 与「没有 WHERE」这个
    合法结果无法区分。

    ★ 顶层集合运算的闸门放在这里而不是各入口：`split_where` 与 `source_table` 都以
    「这段 SQL 是单层单表 SELECT」为前提，两边分别写一遍必然有一边会漏（实测漏的正是
    这两处：`([], [])` 与 `'dws.t'` 双双报成功）。
    """
    if not sql_text or not sql_text.strip():
        raise CaliberParseError("口径 SQL 为空")
    try:
        statements = sqlglot.parse(sql_text, dialect=sqlglot_dialect())
    except Exception as e:  # 转成本模块的错误类型
        raise CaliberParseError(f"口径 SQL 无法解析: {e}") from e
    # 空语句（如结尾多余分号）在 sqlglot 里是 None 元素，同样不可信
    real = [s for s in statements if s is not None]
    if len(real) != 1:
        raise CaliberParseError(f"口径 SQL 含 {len(real)} 条语句，只支持单条")
    tree = real[0]
    # 闸门 6：顶层 UNION / UNION ALL / INTERSECT / EXCEPT（sqlglot 里统一是 SetOperation
    # 的子类）。各分支各有自己的 FROM/WHERE，既没有「顶层唯一 WHERE」也没有「唯一来源表」
    # 这回事，两个入口的前提都不成立。
    if isinstance(tree, exp.SetOperation):
        raise CaliberParseError(
            "口径 SQL 顶层是集合运算（UNION/INTERSECT/EXCEPT），"
            "不是单层单表 SELECT，无法安全反解口径条件或来源表"
        )
    return tree


def split_where(sql_text: str) -> tuple[list[str], list[str]]:
    """返回 `(口径条件片段, 时间窗条件片段)`，均为 SQL 文本。

    无 WHERE 时两者都是空列表（合法）。凡是拆不干净的形态一律抛 `CaliberParseError`
    （四道闸门见模块 docstring）—— 绝不返回一个少了口径条件的结果。
    """
    tree = _parse(sql_text)

    # 闸门 5：HAVING 里的条件两类都放不进去 —— 归口径会被搬进 WHERE（作用在聚合前，
    # 语义变了），归时间窗会被编译器整条重新生成（条件直接丢）。放在这里而不是留给消费方：
    # 实测 `... WHERE d>='{start}' GROUP BY k HAVING SUM(a)>1 AND k<>'x'` 返回
    # `([], ["d >= '{start}'"])` —— 非空、形态正常，调用方看不出 `k <> 'x'` 已经没了。
    if next(tree.find_all(exp.Having), None) is not None:
        raise CaliberParseError(
            "口径 SQL 含 HAVING，其条件作用在聚合之后，"
            "无法安全归入口径条件或时间窗两类，拒绝反解"
        )

    # 闸门 1：只允许顶层唯一一个 WHERE。子查询/CTE 里的 WHERE 属于别的层级，
    # 既不能搬到顶层（语义变了），也不能丢掉（口径没了）。
    wheres = list(tree.find_all(exp.Where))
    if len(wheres) > 1:
        raise CaliberParseError(
            f"口径 SQL 含 {len(wheres)} 个 WHERE（子查询/CTE 内亦有），无法安全反解口径条件"
        )
    if not wheres:
        return [], []
    where = wheres[0]
    # find_all 找到的那个必须就是顶层 SELECT 自己的 WHERE，否则同样是搬错层。
    # 措辞注意：能走到这里的不止「WHERE 长在子查询里」一种 —— 括号包整句
    # （`(SELECT … WHERE x=1)` 顶层是 Subquery）时 WHERE 其实算顶层，但依然挂不到
    # tree 自己身上。所以只说「定位不到单个顶层 SELECT 上」，不断言它在子查询里。
    if tree.args.get("where") is not where:
        raise CaliberParseError(
            "口径 SQL 的 WHERE 无法定位到单个顶层 SELECT 上"
            "（外层被括号或其他结构包裹，或 WHERE 长在内层查询里），无法安全反解口径条件"
        )

    # 顶层 AND 展平；单个谓词不是 And 节点
    conds = list(where.this.flatten()) if isinstance(where.this, exp.And) else [where.this]

    caliber: list[str] = []
    timew: list[str] = []
    for cond in conds:
        text = cond.sql(dialect=sqlglot_dialect())
        if not any(ph in text for ph in _TIME_PLACEHOLDERS):
            caliber.append(text)  # 纯口径谓词：整条原样保留，内部有 OR 也无妨
            continue
        # 闸门 3/4：含时间占位符的谓词若内部还有 AND/OR，说明时间与口径被裹在一起。
        # 时间窗谓词会被编译器整条重新生成，裹在里面的口径条件会随之消失。
        if next(cond.find_all(exp.And, exp.Or), None) is not None:
            raise CaliberParseError(
                f"谓词把时间窗与其他条件裹在一起，无法拆分: {text}"
            )
        timew.append(text)
    return caliber, timew


def source_table(sql_text: str) -> str:
    """唯一来源表，`db.table` 形态。0 或 ≥2 张表时抛。

    ★ 多表口径不进 join 路：本设计只支持「单表口径 + 一跳 join」，
    对已含 JOIN 的口径再叠一跳无法保证口径正确。

    ★ 顶层集合运算由 `_parse`（闸门 6）拦掉：实测
    `source_table("SELECT a FROM dws.t UNION ALL SELECT b FROM dws.t")` 旧行为返回
    `'dws.t'` —— 两个分支恰好同表就被当成「单表」报成功，而这段 SQL 根本不能改写成
    两表 join。
    """
    tree = _parse(sql_text)
    tables = {
        f"{t.db}.{t.name}" if t.db else t.name
        for t in tree.find_all(exp.Table)
    }
    if len(tables) != 1:
        raise CaliberParseError(f"口径 SQL 引用了 {len(tables)} 张表，不支持跨实体下钻")
    return next(iter(tables))
