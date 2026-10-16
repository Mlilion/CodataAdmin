"""参数填充（P4-3；参考方案 §3⑦「不生成 SQL」）。

★ 整个方案幻觉免疫的根本：SQL 不是生成的，是**查表得到（权威 impl.sql_text）+
填占位符**。`is_paid_order=1`、`order_type='subscription'` 这类口径条件写死在权威
SQL 里，LLM 碰不到，天然不会犯 B3（丢过滤条件）。

LLM 能影响的只有：用哪个指标、时间窗、分组维度、附加过滤 —— 全部已过校验器（P4-1）。

安全边界（拼串填充下的注入面）：
- 时间值必须匹配日期/时间戳格式
- 标识符（分组列、过滤字段）必须是纯 identifier
- 操作符走白名单
- 字符串值单引号转义
"""

import re
from datetime import date, datetime

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")

# 操作符白名单：不允许任意字符串拼进 SQL
_OPERATORS = {
    "=": "=", "!=": "!=", "<>": "<>", ">": ">", "<": "<", ">=": ">=", "<=": "<=",
    "in": "IN", "not in": "NOT IN", "like": "LIKE",
}

# GROUP BY 要插在这些子句之前（否则语法错）
_TRAILING_CLAUSES = ("ORDER BY", "LIMIT", "HAVING")
# ORDER BY 只能插在 LIMIT 之前（GROUP BY / HAVING 都在它之前，语序无碍）
_ORDER_TRAILING = ("LIMIT",)
_ORDER_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}


class BindError(ValueError):
    """填充失败。宁可报错也不把可疑 SQL 发到数据库。"""


def _check_time_value(name: str, value) -> str:
    """时间值必须是合法日期/时间戳 —— 拼串填充下这是唯一的注入入口。"""
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    text = str(value).strip()
    if not (_DATE_ONLY.match(text) or _DATETIME.match(text)):
        raise BindError(f"占位符[{name}]的值不是合法日期/时间: {value!r}")
    return text


def _check_identifier(value: str, what: str) -> str:
    text = str(value).strip()
    if not _IDENTIFIER.match(text):
        raise BindError(f"{what}必须是合法标识符: {value!r}")
    return text


def _render_value(value) -> str:
    """标量渲染。数值不加引号（加了在 StarRocks 会隐式转换，影响索引与结果）。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")  # 单引号转义，不破坏引号闭合
    return f"'{escaped}'"


def _render_filter(flt: dict) -> str:
    field = _check_identifier(flt.get("field", ""), "过滤字段")
    raw_op = str(flt.get("op", "=")).strip().lower()
    op = _OPERATORS.get(raw_op)
    if op is None:
        raise BindError(f"不支持的操作符: {flt.get('op')!r}（白名单: {sorted(_OPERATORS)}）")

    value = flt.get("value")
    if op in ("IN", "NOT IN"):
        values = value if isinstance(value, (list, tuple)) else [value]
        if not values:
            raise BindError(f"{field} 的 {op} 取值为空")
        rendered = ", ".join(_render_value(v) for v in values)
        return f"{field} {op} ({rendered})"
    return f"{field} {op} {_render_value(value)}"


def _split_at(sql: str, clauses: tuple[str, ...]) -> tuple[str, str]:
    """把给定的尾部子句切出来，返回 (前半, 尾部)。用于在尾部子句前插入新子句。"""
    upper = sql.upper()
    cut = len(sql)
    for clause in clauses:
        idx = upper.find(clause)
        if idx != -1:
            cut = min(cut, idx)
    return sql[:cut].rstrip(), sql[cut:]


def _split_trailing(sql: str) -> tuple[str, str]:
    """把 ORDER BY / LIMIT / HAVING 等尾部子句切出来，便于在其前插入 GROUP BY。"""
    return _split_at(sql, _TRAILING_CLAUSES)


def _render_order_by(order_by: dict) -> str:
    """渲染 ORDER BY 子句。排序键必须是纯标识符（度量别名或分组列），方向走白名单。

    与 filter/group_by 同源的注入防护：排序字段是 agent 可影响的输入，拼串前必须
    确认它是合法标识符，否则 `field="1; DROP ..."` 之类会进 SQL。
    """
    field = _check_identifier(order_by.get("field", ""), "排序字段")
    raw_dir = str(order_by.get("direction", "desc")).strip().lower()
    direction = _ORDER_DIRECTIONS.get(raw_dir)
    if direction is None:
        raise BindError(
            f"不支持的排序方向: {order_by.get('direction')!r}"
            f"（白名单: {sorted(_ORDER_DIRECTIONS)}）"
        )
    return f"{field} {direction}"


def bind_sql(
    sql_text: str,
    *,
    time: dict | None = None,
    group_by: list[str] | None = None,
    filters: list[dict] | None = None,
    order_by: dict | None = None,
) -> str:
    """把权威口径 SQL 的占位符填成真实值，可选追加分组、过滤与排序。

    参数:
    - sql_text: `impl.sql_text`（权威口径，来自 Release 快照）
    - time: {"start":..., "end":...} 或 {"as_of":...}；as_of 缺失时回退 end
    - group_by: 分组列（纯标识符）
    - filters: 附加过滤（值已过校验器的枚举校验）
    - order_by: {"field": 度量别名或分组列, "direction": "asc"|"desc"}。排序发生在
      权威聚合**之后**，不改口径。字段应已过校验器的白名单校验（度量别名或本次分组列）。

    抛 BindError 而非留下未填的占位符 —— `{start}` 裸奔到数据库会得到语法错或更糟。
    """
    tw = dict(time or {})
    if "as_of" not in tw and tw.get("end"):
        tw["as_of"] = tw["end"]  # "截至某日"的自然含义

    # ---- 填占位符 ----
    missing: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in tw or tw[name] in (None, ""):
            missing.append(name)
            return m.group(0)
        return _check_time_value(name, tw[name])

    sql = _PLACEHOLDER.sub(_sub, sql_text)
    if missing:
        raise BindError(f"占位符缺少取值: {sorted(set(missing))}")
    if leftover := _PLACEHOLDER.findall(sql):
        raise BindError(f"仍有未填占位符: {sorted(set(leftover))}")

    # ---- 追加过滤 ----
    if filters:
        conditions = " AND ".join(_render_filter(f) for f in filters)
        head, tail = _split_trailing(sql)
        connector = "AND" if re.search(r"\bWHERE\b", head, re.IGNORECASE) else "WHERE"
        sql = f"{head}\n  {connector} {conditions}{('\n' + tail) if tail else ''}"

    # ---- 追加分组（唯一"改写"SQL 的地方）----
    if group_by:
        cols = [_check_identifier(c, "分组列") for c in group_by]
        col_list = ", ".join(cols)
        # SELECT 补上分组列，否则结果看不出是哪一组
        sql = re.sub(
            r"(?i)^(\s*SELECT\s+)", rf"\1{col_list}, ", sql, count=1
        )
        head, tail = _split_trailing(sql)
        sql = f"{head}\nGROUP BY {col_list}{('\n' + tail) if tail else ''}"

    # ---- 追加排序 ----
    # 插在 LIMIT 之前（若权威口径自带 LIMIT）；GROUP BY / HAVING 都在 ORDER BY 之前，
    # 语序天然正确。Top-N 的 LIMIT 由 executor 的 enforce_limit 统一负责，这里只排序。
    if order_by:
        clause = _render_order_by(order_by)
        head, tail = _split_at(sql, _ORDER_TRAILING)
        sql = f"{head}\nORDER BY {clause}{('\n' + tail) if tail else ''}"

    return sql
