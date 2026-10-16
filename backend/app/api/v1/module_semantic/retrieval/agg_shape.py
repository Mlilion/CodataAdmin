"""聚合形态判定：经一对多 join 后该聚合会不会被放大。

## 为什么需要这个模块

fan-out 拦截需要回答"这个聚合经一对多关联后还准不准"。dev 真实数仓实测
（2026-07-01 ~ 07-07，经 `order_revenue -one_to_many-> iap_bill_detail`）：

| | 单表 | join 后 |
|---|---|---|
| `SUM(final_revenue)` | 524,147.88 | **628,096.97**（+19.8%） |
| `COUNT(DISTINCT order_id)` | 39,621 | 39,621（不变） |

两条 SQL 都 `ok=True`、都过 EXPLAIN、无任何一层报错 —— 所以必须在编译前判掉。

## 为什么不信 `indicator.additivity`

dev 实测该字段 8/29 为 `None`，且 `order_cnt` 是 `COUNT(DISTINCT order_id)`
却被标成 `'additive'`。以它为主判据会无谓拒绝该指标；反向标错则漏拦真风险。
故本模块以 **sqlglot 解析出的聚合形态**为主判据，`additivity` 只在调用方作辅证。

## 判不了就返回 None

`None` 不等于 `False`。调用方必须把 `None` 当"保守=拒绝"处理 —— 把判不了
当成"不会放大"就等于把风险默认放行。
"""

import sqlglot
from sqlglot import exp

from .dialect import sqlglot_dialect


def _parse(text: str) -> exp.Expression | None:
    """解析失败返回 None —— 上游一律按"判不了"处理，不抛。

    ★ 用 `parse` 而不是 `parse_one`：`parse_one` 只保留第一条语句、静默丢弃其余，
    于是 `"SELECT COUNT(DISTINCT a) FROM t; SELECT SUM(b) FROM t"` 会被当成只有一
    个聚合，下游给出 `False`（=放行）。多语句的真实形态判不了，必须 None。
    """
    if not text or not text.strip():
        return None
    try:
        statements = sqlglot.parse(text, dialect=sqlglot_dialect())
    except Exception:  # 解析失败是预期分支，不是异常路径
        return None
    # 恰好一条才可判；空语句（如结尾多余分号）在 sqlglot 里是 None 元素，同样判不了
    if len(statements) != 1 or statements[0] is None:
        return None
    return statements[0]


def _is_windowed(agg: exp.AggFunc) -> bool:
    """聚合是否挂在窗口下（`… OVER (…)`）。

    窗口聚合不把多行折叠成一行：一对多 join 复制了输入行，输出行就跟着复制，
    `DISTINCT` 也救不回来。所以它不属于本模块支持的"收敛型聚合"形态。
    """
    return isinstance(agg.parent, exp.Window)


def is_amplified_by_join(agg_expr: str) -> bool | None:
    """该聚合经一对多 join 后是否会被放大。

    - `True`：`SUM`/`AVG`/`COUNT(*)`/`COUNT(col)` —— 行数翻倍即结果翻倍
    - `False`：`COUNT(DISTINCT …)` —— 去重，行数翻倍不改结果
    - `None`：解析失败 / 多语句 / 无聚合 / 多个聚合 / 窗口聚合 —— 调用方按拒绝处理
    """
    tree = _parse(agg_expr)
    if tree is None:
        return None
    aggs = list(tree.find_all(exp.AggFunc))
    if len(aggs) != 1:
        return None
    agg = aggs[0]
    # 窗口聚合不收敛行数，去重与否都会随 join 复制的行一起翻倍 —— 判不了，不是 False
    if _is_windowed(agg):
        return None
    # COUNT(DISTINCT x)：sqlglot 把 DISTINCT 挂在 Count.this 上。
    # ★ `isinstance(agg, exp.Count)` 这半个条件是承重的：只判 Distinct 会把
    #   `SUM(DISTINCT x)` 也当成不放大，而它仍在求和，join 后照样放大。
    if isinstance(agg, exp.Count) and isinstance(agg.this, exp.Distinct):
        return False
    return True


def extract_agg_expr(sql_text: str) -> str | None:
    """从整句口径 SQL 里取出唯一的聚合片段。

    ★ 与 `indicator.measure_expression` 互为补充：dev 实测 15/29 指标该字段为空，
    此时只能从口径 SQL 反解。含 0 或 ≥2 个聚合时返回 None（本设计只支持单聚合）。

    窗口聚合同样返回 None：`aggs[0].sql()` 只吐聚合本身、会把 `OVER (…)` 悄悄剥
    掉，返回的片段已不再是原 SQL 的语义，下游拿它判形态必然判错。
    """
    tree = _parse(sql_text)
    if tree is None:
        return None
    aggs = list(tree.find_all(exp.AggFunc))
    if len(aggs) != 1:
        return None
    if _is_windowed(aggs[0]):
        return None
    return aggs[0].sql(dialect=sqlglot_dialect())
