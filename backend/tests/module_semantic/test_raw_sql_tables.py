"""裸 SQL 的表名解析（审计表 `tables` 列的来源）。

## 这个模块存在的理由

`tables` 是审计里回答**「谁在读哪张表」**的那一列 —— 也是开 execute_sql 时最让人
担心的那个问题（设计 §24.4：这条路能读只读账号可见的任何表）。没有它，审计只能
回答"有人查了些什么 SQL"，要人肉去读 SQL 才知道碰了哪张表。

## 为什么解析失败必须返回空列表而不是抛

这是**审计**，不是守卫。解析不出来不该让调用失败，更不该让整条日志丢失 ——
丢了日志等于这次调用在审计里不存在，那比"表名这一格是空的"糟糕得多。
"""

import pytest

from app.api.v1.module_semantic.rawsqllog.sql_tables import extract_tables

# ---------------------------------------------------------------- 基本形状


def test_single_table():
    assert extract_tables("SELECT a FROM dws.t1") == ["dws.t1"]


def test_table_without_database_qualifier():
    """不带库名的照原样返回 —— 审计不做补全（补错了比不补更误导）。"""
    assert extract_tables("SELECT a FROM t1") == ["t1"]


def test_join_collects_both_sides():
    sql = "SELECT a FROM dws.t1 JOIN dws.t2 ON t1.id = t2.id"
    assert extract_tables(sql) == ["dws.t1", "dws.t2"]


def test_deduplicates_self_join():
    """同一张表出现两次只记一次（审计要看的是"碰了哪些表"）。"""
    sql = "SELECT a FROM dws.t1 x JOIN dws.t1 y ON x.id = y.pid"
    assert extract_tables(sql) == ["dws.t1"]


def test_preserves_appearance_order():
    """保留出现顺序而非排序 —— 第一张通常是主表，顺序本身有信息。"""
    sql = "SELECT a FROM dws.zzz JOIN dws.aaa ON 1=1"
    assert extract_tables(sql) == ["dws.zzz", "dws.aaa"]


# ---------------------------------------------------------------- 嵌套结构


def test_cte_collects_underlying_table_not_the_alias():
    """★ CTE 名不是表。

    `WITH x AS (SELECT ... FROM dws.real)` 里 `x` 是**查询别名**，把它记成表名会让
    审计出现一堆根本不存在的"表"，而真正被读的 dws.real 反而漏掉 —— 那是错的方向
    （审计漏记比多记严重）。
    """
    sql = "WITH x AS (SELECT a FROM dws.real_t) SELECT * FROM x"
    assert extract_tables(sql) == ["dws.real_t"]


def test_cte_named_after_the_table_it_reads():
    """★ CTE 名与真实表同名时，真实表不能跟着一起消失。

    `WITH orders AS (SELECT * FROM orders)` 是最常见的写法 —— 拿表名给 CTE 命名。
    如果 CTE 名当成全局黑名单用，这条 SQL 的审计结果是空的：明明读了 orders，
    审计却声称"谁都没读过表"。**漏记比多记危险得多** —— 多记一个不存在的表名，
    人能看出来；漏记一张真读过的表，没人会去怀疑。
    """
    sql = "WITH orders AS (SELECT * FROM orders) SELECT * FROM orders"
    assert extract_tables(sql) == ["orders"]


def test_cte_in_later_statement_does_not_erase_earlier_real_table():
    """★ 一条语句里的 CTE 名不得抹掉另一条语句里的真实表读取。

    第一条语句实读 t1；第二条语句才把 t1 定义成 CTE。CTE 名的作用域是它所在的那
    条语句，跨语句去黑名单化会把第一条的真实读取吃掉 —— 审计从此看不见有人读过
    t1（漏记方向）。
    """
    sql = "SELECT * FROM t1; WITH t1 AS (SELECT 1) SELECT * FROM t1"
    assert extract_tables(sql) == ["t1"]


def test_cte_in_earlier_statement_does_not_erase_later_real_table():
    """★ 反向同理：前一条语句的 CTE 名不得抹掉后一条语句的真实表。

    第一条语句里 agg 是 CTE；第二条语句里 agg 是真表。按语句作用域解析才对，
    否则又是一次静默漏记。
    """
    sql = "WITH agg AS (SELECT 1) SELECT * FROM agg; SELECT * FROM agg"
    assert extract_tables(sql) == ["agg"]


def test_qualified_table_not_shadowed_by_same_named_cte():
    """带库名限定的表不会被同名 CTE 遮蔽 —— `dws.orders` 与 CTE `orders` 是两码事。"""
    sql = "WITH orders AS (SELECT 1) SELECT * FROM dws.orders"
    assert extract_tables(sql) == ["dws.orders"]


def test_subquery_in_from():
    sql = "SELECT * FROM (SELECT a FROM dws.inner_t) z"
    assert extract_tables(sql) == ["dws.inner_t"]


def test_subquery_in_where():
    sql = "SELECT a FROM dws.t1 WHERE id IN (SELECT id FROM dws.t2)"
    assert extract_tables(sql) == ["dws.t1", "dws.t2"]


def test_union_collects_all_branches():
    sql = "SELECT a FROM dws.t1 UNION ALL SELECT a FROM dws.t2"
    assert extract_tables(sql) == ["dws.t1", "dws.t2"]


# ---------------------------------------------------------------- 不抛


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "SELECT FROM WHERE",          # 语法不完整
    "这不是 SQL",
    "SELECT a FROM",              # 截断
])
def test_never_raises_returns_empty(bad):
    """★ 解析失败一律空列表。审计不得因为解析不了而丢掉整条记录。"""
    assert extract_tables(bad) == []


def test_no_table_query_returns_empty():
    """无表查询（如 SELECT 1）合法且返回空 —— 不是失败。"""
    assert extract_tables("SELECT 1") == []


@pytest.mark.parametrize("bad", [None, 123, 12.5, b"SELECT 1", ["SELECT 1"], {}])
def test_non_str_input_returns_empty(bad):
    """★ 非字符串入参也返回空列表，不抛。

    审计代码不能因为拿到个 None / int 就把整条日志连带打掉 —— 同上，丢日志比
    表名这格空着糟糕得多。
    """
    assert extract_tables(bad) == []


def test_guard_covers_the_whole_body_not_just_parse():
    """★ "永不抛"必须对整个函数体成立，不只是 parse 那一行。

    sqlglot 的 generator 遇到渲染不了的节点会抛（UnsupportedError / ValueError），
    build_scope 也会抛。只把 parse 包进 try 里，契约就是假的。

    这里两种情况都是**一张也没解析出来**（生成阶段全挂 / 作用域阶段就挂了），空列表
    是当时手上真实的结果。注意别把这条读成"出错就清空"：已经收集到的表名绝不能因为
    后面某个节点渲染失败而被丢掉 —— 那等于审计声称"谁都没读过表"，与原本那个
    Critical（CTE 黑名单抹掉真实表读取）是同一个失败方向，漏记比多记危险得多。
    这条性质由 `test_one_unrenderable_table_does_not_discard_the_others` 钉住。
    """
    from unittest.mock import patch

    from sqlglot.errors import UnsupportedError

    from app.api.v1.module_semantic.rawsqllog import sql_tables

    sql = "SELECT a FROM dws.t1 JOIN dws.t2 ON 1=1"

    # .sql() 生成阶段抛：每一张都渲染不出来 → 手上确实一张都没有
    with patch.object(sql_tables.exp.Table, "sql", side_effect=UnsupportedError("boom")):
        assert extract_tables(sql) == []

    # 作用域构建阶段抛：还没进到收集循环 → 同样一张都没有
    with patch.object(sql_tables, "build_scope", side_effect=ValueError("boom")):
        assert extract_tables(sql) == []


def test_one_unrenderable_table_does_not_discard_the_others():
    """★ 单个节点渲染失败只能吃掉它自己，不能把已经收集到的表名一起清空。

    第 3 张表渲染时抛 UnsupportedError，前两张（dws.a / dws.b）已经在手上了。
    如果整个循环共用一个 try，这两张会跟着一起被丢掉，审计结果变成空 ——
    与原本那个 Critical 一模一样的静默漏记，只是换了一扇更窄的门进来。
    第 4 张（dws.d）也必须继续收，一个坏节点不该终止后面的遍历。
    """
    from unittest.mock import patch

    from sqlglot.errors import UnsupportedError

    from app.api.v1.module_semantic.rawsqllog import sql_tables

    sql = "SELECT * FROM dws.a JOIN dws.b ON 1=1 JOIN dws.c ON 1=1 JOIN dws.d ON 1=1"
    # find_all 广度优先，渲染顺序 a, b, c, d —— 第 3 次调用（dws.c）抛
    renders = ["dws.a", "dws.b", UnsupportedError("boom"), "dws.d"]

    with patch.object(sql_tables.exp.Table, "sql", side_effect=renders):
        assert extract_tables(sql) == ["dws.a", "dws.b", "dws.d"]


def test_breadth_first_outermost_table_comes_first():
    """顺序是广度优先（最外层在前），不是"文本里第一次出现"。

    子查询在 SQL 文本里写在前面，但最外层的 dws.outer_t 才是主表 —— 广度优先
    正好把主表排在第一个，符合"第一张通常是主表"这个用意。
    """
    sql = "SELECT * FROM (SELECT 1 FROM dws.inner_t) z JOIN dws.outer_t ON 1=1"
    assert extract_tables(sql) == ["dws.outer_t", "dws.inner_t"]


# ---------------------------------------------------------------- 病理输入不得卡死


def _bare_join_chain(n: int) -> str:
    """不带 ON 的裸 JOIN 链 —— sqlglot 27.8 在这个形状上指数级回溯。"""
    joins = " ".join(f"JOIN dws.t{i}" for i in range(1, n))
    return f"SELECT * FROM dws.t0 {joins}"


def test_unmatched_join_chain_does_not_hang_and_still_returns_all_tables():
    """★★ 病理输入必须**快**且**不丢表名**。

    实测未加闸时：10 张 0.01s、16 张 0.39s、20 张 6.3s、23 张 50.6s —— 卡死而不抛，
    try/except 完全挡不住。这条路吃的是 agent 给的 SQL，等于一个可达的 DoS。

    ★ 降级路径绝不能返回 `[]`：这种输入恰恰最可能是攻击探测，让审计声称"谁都没读过表"
    是最坏的失败方向，那会让这道闸比它修的 bug 更糟。所以断言 23 张表**一张不少**。
    """
    import time

    n = 23
    sql = _bare_join_chain(n)
    assert len(sql) < 400, "这个病理输入本身很短 —— 闸不能拿输入长度当判据"

    start = time.perf_counter()
    tables = extract_tables(sql)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"病理输入耗时 {elapsed:.3f}s，闸没生效"
    assert tables == [f"dws.t{i}" for i in range(n)], "降级路径丢了表名(漏记)"


def test_long_unmatched_join_chain_still_recovers_every_name():
    """60 张表的裸 JOIN 链（未加闸时跑不完）也要一张不少地取回来。"""
    import time

    n = 60
    start = time.perf_counter()
    tables = extract_tables(_bare_join_chain(n))
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"耗时 {elapsed:.3f}s"
    assert tables == [f"dws.t{i}" for i in range(n)]


def test_benign_matched_join_chain_still_goes_through_parse():
    """★ 判据是"没配对的 JOIN 数"，不是表数量：60 张带 ON 的 JOIN 链 parse 只要 ~0.001s，
    不该被闸误伤 —— 误伤的代价是白丢 CTE 名剔除。

    用 CTE 形状来证明确实走了正常 parse 路：只有 parse 路才会把 CTE 名剔掉。
    """
    n = 60
    joins = " ".join(f"JOIN dws.t{i} ON 1=1" for i in range(1, n))
    sql = f"WITH cte_x AS (SELECT 1 FROM dws.base) SELECT * FROM dws.t0 {joins}"

    tables = extract_tables(sql)

    assert "cte_x" not in tables, "被闸误伤了 —— 降级路径不剔 CTE 名"
    assert "dws.base" in tables
    assert tables[0] == "dws.t0"
    assert len(tables) == n + 1  # t0..t59 + dws.base


def test_comma_join_of_many_tables_is_unaffected():
    """逗号连接（无 JOIN token）不受闸影响，仍走正常 parse。"""
    n = 40
    sql = "SELECT * FROM " + ", ".join(f"dws.t{i}" for i in range(n))

    assert extract_tables(sql) == [f"dws.t{i}" for i in range(n)]


def test_a_few_unmatched_joins_still_go_through_parse():
    """阈值以下（这里 3 个没配对的 JOIN）照旧走 parse —— CTE 名仍被剔掉。"""
    sql = (
        "WITH cte_y AS (SELECT 1 FROM dws.base) "
        "SELECT * FROM dws.a JOIN dws.b JOIN dws.c JOIN cte_y"
    )

    tables = extract_tables(sql)

    assert "cte_y" not in tables, "阈值以下不该走降级路径"
    assert "dws.base" in tables


# ------------------------------------------------- 闸不能被"别处的 ON"抵消掉
#
# ★ 数 JOIN 与数 ON/USING 必须**按相邻作用域配对**，不能全文档相减。全文档相减的话
# 任何位置上的一个 ON 都能抵掉一个裸 JOIN —— 而 ON 这个词在 SQL 里到处都能出现
# （`CREATE INDEX x ON t`），tokenizer 又是无上下文的（连尾巴上光秃秃的 `ON ON ON`
# 也照数）。于是那道闸变成"随便加几个 ON 就能关掉"，病理输入原样进 parse。
# 两个形状都是 agent 能直接提交的、都不到 1KB、都在实测里让闸失效（6s+）。


def test_unrelated_on_tokens_cannot_disable_the_guard():
    """★★ 别的语句里的 ON 不得抵消本语句的裸 JOIN（多语句前缀绕过）。

    `CREATE INDEX iN ON dws.z (c);` 每条贡献一个 ON、零个 JOIN。按全文档相减，
    20 条这样的前缀就能把 19 个裸 JOIN 的计数压到 0，闸不触发 —— 实测 838 字符
    的输入 parse 花 6.19s / 6.48s。闸的判据必须是"这个 JOIN 自己有没有配到 ON"。
    """
    import time

    prefix = "CREATE INDEX iN ON dws.z (c);" * 20
    sql = prefix + _bare_join_chain(20)
    assert len(sql) < 1024, "这个绕过本身很短 —— 长度不是判据"

    start = time.perf_counter()
    tables = extract_tables(sql)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"耗时 {elapsed:.3f}s —— 前缀里的 ON 把闸抵消掉了"
    # 降级路径同样不得漏记（漏记比多记糟）：裸 JOIN 链那 20 张必须都在
    for i in range(20):
        assert f"dws.t{i}" in tables, f"降级路径漏了 dws.t{i}"


def test_trailing_bare_on_tokens_cannot_disable_the_guard():
    """★★ 同一条语句尾巴上的裸 ON 也不得抵消（tokenizer 无上下文）。

    `... WHERE x IN (1) ON ON ON ...` 里的 ON 不属于任何 JOIN，可全文档相减照数。
    实测 306 字符、11 个尾随 ON 就能把计数压到阈值内：parse 6.79s，而且这条 SQL
    本身语法非法 → parse 抛 → `extract_tables` 返回 `[]`：**慢**且**全漏记**，
    正是这张表最坏的失败方向。
    """
    import time

    sql = _bare_join_chain(20) + " WHERE x IN (1)" + " ON" * 11
    assert len(sql) < 1024

    start = time.perf_counter()
    tables = extract_tables(sql)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"耗时 {elapsed:.3f}s —— 尾随裸 ON 把闸抵消掉了"
    assert tables, "全漏记：闸没生效、parse 又失败，审计声称谁都没读过表"
    for i in range(20):
        assert f"dws.t{i}" in tables, f"降级路径漏了 dws.t{i}"


def test_fallback_also_collects_comma_separated_tables():
    """★★ 降级路径必须把**逗号连接**的表也捞上 —— 否则混合形状静默漏记。

    `FROM dws.a, dws.b, dws.c JOIN dws.j0 … JOIN dws.j9`：裸 JOIN 触发降级，
    而降级扫描只认 FROM/JOIN 后面紧跟的那一个标识符链，于是 `dws.b`、`dws.c`
    被丢掉。漏的是**真读过的表**，人不会去怀疑一份看起来正常的表名列表。
    """
    joins = " ".join(f"JOIN dws.j{i}" for i in range(10))
    sql = f"SELECT * FROM dws.a, dws.b, dws.c {joins}"

    tables = extract_tables(sql)

    assert tables == ["dws.a", "dws.b", "dws.c"] + [f"dws.j{i}" for i in range(10)]


def test_aliased_comma_tables_in_fallback_keep_table_names_not_aliases():
    """★ 降级路径收逗号表时不能把别名当成表名（多记一个不存在的"表"还算轻，
    但 `dws.b AS y` 收成 `y` 会让治理页出现一个查无此表的名字）。"""
    joins = " ".join(f"JOIN dws.j{i}" for i in range(10))
    sql = f"SELECT * FROM dws.a x, dws.b AS y, dws.c z {joins}"

    tables = extract_tables(sql)

    assert "x" not in tables and "y" not in tables and "z" not in tables
    assert tables[:3] == ["dws.a", "dws.b", "dws.c"]


def test_fallback_collects_comma_tables_after_join_too():
    """★★ `JOIN a, b` 的逗号链也必须收 —— 只跟 FROM 的逗号是漏记。

    逗号比 JOIN 结合得松，`JOIN dws.public_b, dws.SECRET_SALARY, dws.SECRET_TOKENS`
    在 MySQL/StarRocks 里合法，那两张 SECRET 表是**和 public_b 平级**被读的表，不是
    "下一个 JOIN 的事"。实测这条 222 字符、agent 能直接提交的 SQL 会让两张真表从审计
    里静默消失，而 `tables` 列看起来完全正常 —— 没人会去怀疑它。
    parse 路是对的，只有降级路径漏；与前一轮修的"逗号表被裸 JOIN 带丢"同一个缺陷类。
    """
    pad = " ".join(f"JOIN dws.pad{i}" for i in range(10))
    sql = (
        "SELECT * FROM dws.public_a JOIN dws.public_b, dws.SECRET_SALARY, "
        f"dws.SECRET_TOKENS {pad}"
    )
    assert len(sql) < 400, "这个漏记形状本身很短 —— 长度不是判据"

    tables = extract_tables(sql)

    assert "dws.SECRET_SALARY" in tables, "降级路径漏了 JOIN 后逗号链上的真实表"
    assert "dws.SECRET_TOKENS" in tables, "降级路径漏了 JOIN 后逗号链上的真实表"
    assert tables == (
        ["dws.public_a", "dws.public_b", "dws.SECRET_SALARY", "dws.SECRET_TOKENS"]
        + [f"dws.pad{i}" for i in range(10)]
    )


def test_fallback_collects_comma_tables_after_a_cross_join():
    """★ 同一个病根的另一支：CROSS JOIN 尾巴上的逗号链同样不能丢。

    `… CROSS JOIN dws.d9, dws.SECRET_X` —— 前面的裸 JOIN 链把这条 SQL 推进降级路径，
    而降级路径不跟 JOIN 后的逗号，于是 `dws.SECRET_X` 静默消失。
    """
    pad = " ".join(f"JOIN dws.d{i}" for i in range(9))
    sql = f"SELECT * FROM dws.a {pad} CROSS JOIN dws.d9, dws.SECRET_X"

    tables = extract_tables(sql)

    assert "dws.SECRET_X" in tables, "降级路径漏了 CROSS JOIN 后逗号链上的真实表"


def test_fallback_does_not_take_using_or_group_by_columns_as_tables():
    """★ 跟了 JOIN 后的逗号，不能顺手把**列名**收成表名。

    `USING (a, b)` / `GROUP BY a, b` / `IN (1, 2)` / `ORDER BY a, b` 里的逗号后面是
    列或字面量，不是表。多记允许，但把 `col_x` 记成表会让这一列直接失去可用性 ——
    治理页上分不清哪个名字是真表。这些 token（USING / GROUP BY / WHERE / …）本身
    就该终止逗号链。
    """
    joins = " ".join(f"JOIN dws.j{i}" for i in range(10))
    sql = (
        f"SELECT * FROM dws.a {joins} JOIN dws.b USING (col_u1, col_u2) "
        "WHERE x IN (1, 2) GROUP BY col_g1, col_g2 ORDER BY col_o1, col_o2 LIMIT 1, 2"
    )

    tables = extract_tables(sql)

    for junk in ("col_u1", "col_u2", "col_g1", "col_g2", "col_o1", "col_o2"):
        assert junk not in tables, f"降级路径把列名 {junk} 记成了表"
    assert tables == ["dws.a"] + [f"dws.j{i}" for i in range(10)] + ["dws.b"]


@pytest.mark.parametrize("joins", [
    # ON 属于它前面那个 JOIN —— 一个都不该被算成没配对
    "JOIN dws.b ON cte_z.i = dws.b.i JOIN dws.c USING (k)",
    # 子查询里带 WHERE，ON 在括号外面
    "JOIN (SELECT c FROM dws.b WHERE c > 1) z ON z.c = cte_z.c",
    # CROSS JOIN 本来就不需要 ON，parse 也不慢（实测 20 张 0.000s）
    "CROSS JOIN dws.b CROSS JOIN dws.c",
    # ★ 阈值(8)以上的 CROSS JOIN 链：CROSS JOIN **定义上**不带 ON，把一整条链算成
    # "没配对"会把一条合法且快（实测 23 张 0.003s）的 SQL 推进降级路径 —— 代价是 CTE 名
    # 被记成表，正是 CTE 剔除要防的幻影表。阈值以下的 3 张链盖不住这个，必须过阈值。
    " ".join(f"CROSS JOIN dws.c{i}" for i in range(12)),
    # NATURAL JOIN 同理（NATURAL / NATURAL LEFT 定义上都不带 ON）
    " ".join(f"NATURAL JOIN dws.n{i}" for i in range(12)),
    " ".join(f"NATURAL LEFT JOIN dws.n{i}" for i in range(12)),
])
def test_benign_join_shapes_still_take_the_parse_path(joins):
    """★ 相邻配对不能把正常写法误判成病理输入 —— 误伤的代价是白丢 CTE 名剔除。

    ★ CTE 必须被 **FROM 引用**才能验出降级：降级扫描只收 FROM/JOIN 后面的标识符，
    `WITH cte_z AS (...)` 里那个定义位置的 cte_z 它根本不看。所以"包一层没引用的 CTE
    再断言 cte_z 不在结果里"是句空话 —— 两条路都能过。这里让 cte_z 出现在 FROM 上，
    只有 parse 路才会把它剔掉。

    再补一条直接判据：降级函数一次都不能被调到。
    """
    from unittest.mock import patch

    from app.api.v1.module_semantic.rawsqllog import sql_tables

    sql = f"WITH cte_z AS (SELECT 1 FROM dws.base) SELECT * FROM cte_z {joins}"

    with patch.object(
        sql_tables, "_scan_tables_linearly", side_effect=AssertionError("走了降级路径")
    ):
        tables = extract_tables(sql)

    assert "cte_z" not in tables, "被闸误伤了 —— 降级路径不剔 CTE 名"
    assert "dws.base" in tables


def test_cross_join_exemption_does_not_let_bare_joins_through():
    """★ CROSS/NATURAL 豁免不得成为闸的新绕过口。

    只有"不带 ON 的**裸** JOIN"才是指数级回溯的那个形状；CROSS/NATURAL 前缀不该
    给裸 JOIN 提供掩护。这里在一堆 CROSS JOIN 中间夹 20 个裸 JOIN，闸必须照样触发。
    """
    import time

    crosses = " ".join(f"CROSS JOIN dws.c{i}" for i in range(10))
    bare = " ".join(f"JOIN dws.t{i}" for i in range(20))
    sql = f"SELECT * FROM dws.a {crosses} {bare}"

    start = time.perf_counter()
    tables = extract_tables(sql)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"耗时 {elapsed:.3f}s —— CROSS 豁免把闸抵消掉了"
    for i in range(20):
        assert f"dws.t{i}" in tables, f"降级路径漏了 dws.t{i}"


def test_degraded_fallback_is_logged_at_warning_not_debug():
    """★ 降级这条日志必须是 WARNING —— 生产日志级别是 INFO，DEBUG 根本不落盘。

    它同时报两件要人看见的事：(1) 大概率是攻击探测；(2) 这一行审计的准确度是**已知
    降级**的（CTE 名可能被记成表）。只在病理输入上触发，不会吵。
    """
    from unittest.mock import patch

    from app.core.logger import logger

    warnings: list[str] = []
    debugs: list[str] = []
    sql = _bare_join_chain(20)

    with (
        patch.object(logger, "warning", lambda msg, *a, **kw: warnings.append(str(msg))),
        patch.object(logger, "debug", lambda msg, *a, **kw: debugs.append(str(msg))),
    ):
        assert extract_tables(sql)

    assert any("线性扫描" in m for m in warnings), (
        f"降级没打 WARNING（生产 INFO 级别下 DEBUG 不可见）: warning={warnings} debug={debugs}"
    )
    assert not any("线性扫描" in m for m in debugs), "降级日志还留在 DEBUG 上"


def test_dialect_is_shared_not_a_private_copy():
    """★ 方言 helper 必须真的来自 retrieval.dialect，不能是本地另写/中转的一份。

    方言现在由配置决定，不能再在任一解析模块里冻结 `_DIALECT = "starrocks"`。
    单靠返回值断言没用：本地写一个同名函数也可能刚好返回 `"starrocks"`。所以这里同时
    钉住运行时对象身份、AST import 来源，并禁止本地再声明 `_DIALECT` 或
    `sqlglot_dialect`。
    """
    import ast
    import inspect

    from app.api.v1.module_semantic.rawsqllog import sql_tables
    from app.api.v1.module_semantic.retrieval import dialect
    from app.api.v1.module_semantic.retrieval.dialect import sqlglot_dialect

    # ---- 运行时同源：必须是同一个对象，不是"恰好相等的另一份字面量"
    assert sql_tables.sqlglot_dialect is sqlglot_dialect, (
        "sql_tables.sqlglot_dialect 必须就是 retrieval.dialect.sqlglot_dialect 那个对象，"
        "本地另写一份会和 join 编译漂移"
    )

    # ---- AST：import 的来源模块必须解析到 retrieval.dialect，且不得再赋值
    tree = ast.parse(inspect.getsource(sql_tables))
    pkg = sql_tables.__name__.rsplit(".", 1)[0]  # app...rawsqllog

    sources = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "sqlglot_dialect" for a in node.names):
            # 相对 import 要按本模块所在包解析（level=1 → 同包，2 → 上一层……）
            if node.level:
                base = pkg.rsplit(".", node.level - 1)[0] if node.level > 1 else pkg
                sources.add(f"{base}.{node.module}" if node.module else base)
            else:
                sources.add(node.module or "")
    assert sources, "sqlglot_dialect 必须从 retrieval.dialect import，而不是本地再写一份"
    assert sources == {dialect.__name__}, (
        f"sqlglot_dialect 只能从 {dialect.__name__} import，实际来源 {sorted(sources)}"
        "（本地 shim 中转同样会漂移）"
    )

    assigned = [
        node
        for node in ast.walk(tree)
        if (
            (isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "_DIALECT" for t in node.targets))
            or (isinstance(node, ast.AnnAssign | ast.AugAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "_DIALECT")
            or (isinstance(node, ast.FunctionDef) and node.name == "sqlglot_dialect")
        )
    ]
    assert not assigned, "sql_tables.py 里不得本地声明方言（会与 join 编译漂移）"
