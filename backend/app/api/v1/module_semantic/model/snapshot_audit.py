"""快照审计：权威口径 SQL 与物理列的漂移守卫（参考方案 §9 坑 6）。

## 为什么这层必须存在

`impl.sql_text` 是全链路唯一**直接拿去执行、却无任何校验**的东西。方案 §3⑦ 的立论是
「SQL 不生成、查表得到」——幻觉免疫的代价是：口径正确性完全依赖人工，而人工写的 SQL
会因**上游改表**而失效。逐个环节核对，没有一处能拦住：

| 环节 | 查什么 | 拦幽灵列 |
|---|---|---|
| `schema.py`/`model.py` 的 `sql_text` validator | 非空 | ❌ |
| `completeness.field_completeness` | `physical_column_id` 是否 orphan | ❌ 只看 field，不读 SQL |
| `retrieval/validator.py` 12 条规则 | 参数（维度/枚举/时间窗） | ❌ 一条都不读 SQL 文本 |
| `retrieval/executor.guard_readonly` | DDL/DML 关键字 | ❌ 只防写 |
| `retrieval/executor` 的 EXPLAIN 预检 | 数据库判可执行性 | ✅ **但已在取数那一刻** |

2026-07-31 上游把 `dws_order_revenue_df` 重构为 `dws_revenue_order_df` 并删掉
`is_paid_order` 等 5 列时，14 个写着 `WHERE is_paid_order = 1` 的 impl 一路通过
「发布全绿」，直到用户真去问数才炸在 EXPLAIN 上。

实测 EXPLAIN 确实会报 `Column 'is_paid_order' cannot be resolved`，所以后果**不是
静默返回错数**，而是「发布时说全绿、取数时才炸，且错误面向终端用户」。本模块把发现点
从"用户提问"左移到"发布"。

## 判据与边界

判据锚在快照自身：`entities[].fields[]` 声明的名字集合 vs `impls[].sql_text` 引用的
标识符，同源于一次发布，不依赖外部 YAML 或数仓连接。

★ 已知边界（有意为之，宁漏报不误报）：
- 这是正则启发式，不是 SQL parser。CTE 名、窗口函数别名等复杂构造可能漏报。
- **漏报优于误报**：误报会让合法模型无法发布（把发布链路焊死），漏报只是回到现状
  （EXPLAIN 兜底）。所以词表宁可宽松。
"""

import re

# SQL 关键字与函数名 —— 长得像标识符但不是列
_SQL_WORDS = frozenset({
    "select", "from", "where", "and", "or", "not", "is", "null", "as",
    "case", "when", "then", "else", "end", "if", "ifnull", "nullif", "coalesce",
    "sum", "count", "avg", "max", "min", "distinct", "abs", "round", "floor", "ceil",
    "in", "between", "like", "rlike", "regexp", "on", "using",
    "join", "left", "right", "inner", "outer", "cross", "full",
    "group", "by", "order", "having", "limit", "offset", "asc", "desc",
    "current_date", "current_timestamp", "now", "date_sub", "date_add", "date_diff",
    "datediff", "date_trunc", "date_format", "to_date", "unix_timestamp",
    "interval", "day", "days", "week", "month", "months", "quarter", "year", "years",
    "hour", "minute", "second",
    "cast", "convert", "int", "bigint", "decimal", "double", "float", "varchar",
    "string", "date", "datetime", "timestamp", "boolean",
    "true", "false", "unknown",
    "over", "partition", "row_number", "rank", "dense_rank", "lag", "lead",
    "union", "all", "with", "exists", "any", "some", "distinctrow",
    "concat", "substr", "substring", "trim", "lower", "upper", "length",
})

# 数仓分层前缀：以此开头的标识符是库名/表名，不是列
_LAYER_PREFIXES = ("dws", "dwd", "dim", "ads", "ods", "dm_", "tmp")


def _strip_noise(sql: str) -> str:
    """剥掉会被误认成列名的四类东西。

    这里每一条都是踩出来的：第一版没剥，21 条合法 impl 报出 20 条假异常 ——
    误报率高的守卫等于没有守卫，没人会去看它。
    """
    s = re.sub(r"--[^\n]*", " ", sql)              # 行注释（口径 SQL 常写中文说明）
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)  # 块注释
    s = re.sub(r"'[^']*'", " ", s)                  # 字符串字面量（枚举值 'subscription'）
    s = re.sub(r'"[^"]*"', " ", s)
    s = re.sub(r"\{\w+\}", " ", s)                  # 占位符 {start}/{end}/{as_of}
    s = re.sub(r"(?i)\bAS\s+\w+", " ", s)           # 输出别名 AS sub_revenue
    # 限定名只去掉前缀保留列名：`a.product_type` → `product_type`（否则别名限定的
    # 幽灵列会整段消失，成为漏报）。`dws.dws_revenue_order_df` → 表名，由前缀规则滤掉。
    s = re.sub(r"\b\w+\.(\w+)\b", r" \1 ", s)
    return s


def _identifiers(sql: str) -> set[str]:
    """抽出 SQL 里被当作列引用的标识符（已剥噪声、已去关键字）。"""
    return {
        t for t in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", _strip_noise(sql).lower())
        if t not in _SQL_WORDS
    }


def _declared_names(snapshot: dict) -> set[str]:
    """快照声明的可引用名字：物理列名 + 字段 code + expr 内引用的列。

    ★ code 也算：语义层允许 `field.code` 与物理列名不同（`code=final_revenue` 绑
    `column=revenue_usd`）。口径 SQL 里出现哪个都合法 —— 守卫要抓的是「**两者都不是**」
    的名字（真幽灵列），而非强制只能写物理列名（那属风格约束，不该由它管）。
    """
    names: set[str] = set()
    for entity in snapshot.get("entities") or []:
        for f in entity.get("fields") or []:
            if col := f.get("column"):
                names.add(str(col).lower())
            if code := f.get("code"):
                names.add(str(code).lower())
            if expr := f.get("expr"):
                names |= _identifiers(str(expr))
    return names


def _physical_names(snapshot: dict) -> set[str]:
    """快照里出现的库/表/数据源名 —— 它们不是列。"""
    names: set[str] = set()
    for entity in snapshot.get("entities") or []:
        if physical := entity.get("physical"):
            for key in ("table", "database", "datasource"):
                if v := physical.get(key):
                    names.add(str(v).lower())
    return names


def audit_snapshot(snapshot: dict) -> list[str]:
    """审计已编译快照，返回「口径 SQL 引用了未声明的列」清单。空 = 无漂移。

    返回人类可读的问题串（每条含 indicator.role 与幽灵列名），供发布接口直接
    拼进错误消息，也供 CI 断言。
    """
    declared = _declared_names(snapshot)
    physical = _physical_names(snapshot)
    problems: list[str] = []

    for ind in snapshot.get("indicators") or []:
        for impl in ind.get("impls") or []:
            ghosts = sorted(
                name for name in _identifiers(impl.get("sql_text") or "")
                if name not in declared
                and name not in physical
                and not name.startswith(_LAYER_PREFIXES)
            )
            if ghosts:
                problems.append(
                    f"指标[{ind.get('code')}]的{impl.get('role')}口径引用了"
                    f"模型未声明的列: {', '.join(ghosts)}"
                )
    return problems
