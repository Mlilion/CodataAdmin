"""从裸 SQL 里解析出被读的表名（审计 `tables` 列的来源）。

## 判据

用 sqlglot 解析后遍历 `exp.Table` 节点。**CTE 名同样是 `exp.Table` 节点** ——
`WITH x AS (SELECT a FROM dws.real_t) SELECT * FROM x` 的 `find_all(exp.Table)`
返回 `['x', 'dws.real_t']`，`x` 就在里头。所以必须显式把 CTE 引用剔掉，
下面那段排除代码不是死代码，删了审计里会冒出一堆根本不存在的"表"。

排除按**作用域**做，不是按名字做全局黑名单：CTE 名的作用域是它所在的那条语句，
拿它去跨语句、跨作用域地过滤表名会把真实的表读取吃掉（`WITH orders AS
(SELECT * FROM orders)` 是最常见的写法，全局黑名单会让这条 SQL 的审计结果为空）。

## 失败一律返回空列表

这是**审计**不是守卫：解析不出来不该让调用失败，更不该让整条日志丢失。
丢日志等于这次调用在审计里不存在，比"表名这格是空的"糟糕得多。

同理，宁可多记也不能漏记：多出来的表名人能看出来，漏掉一张真读过的表
没人会去怀疑。所以判不准的时候一律**保留**表名。这也意味着**排除并不彻底**：
`build_scope` 返回 None 时（例如 `EXPLAIN WITH a AS (...) SELECT * FROM a`
→ `['a', 'dws.s']`）CTE 名仍会被记成表。这是刻意接受的多记，别把上面那段
排除读成"CTE 一定不会出现"。

## 病理输入要绕开 parse（不能靠 try/except）

sqlglot 27.8 在**不带 ON 的裸 JOIN 链**上是指数级回溯：实测 10 张表 0.01s、
16 张 0.39s、20 张 6.3s、23 张 50.6s。这函数跑在取数主链上、吃的是 agent 给的 SQL，
而 try/except 对此**无能为力** —— 它是卡死不是抛异常，卡死正是"永不失败"契约唯一
真正被破掉的方式。所以先用 `sqlglot.tokenize`（线性，微秒级）数"没配对的 JOIN"，
超过阈值就不进 `parse`，改走线性 token 扫描把表名捞回来（见 `_scan_tables_linearly`）。

判据是**没配对的 JOIN 数**，不是输入长度也不是表数量：23 张表的病理输入只有 ~297 字符，
而 60 张表的 `JOIN ... ON 1=1` 链 parse 只要 ~0.001s —— 那两个变量都是错的。

而"配对"必须按**相邻**判定，不能全文档地拿 ON 总数去减 JOIN 总数。全文档相减等于
"任意位置的一个 ON 都能抵掉一个裸 JOIN"，而 `ON` 这个词在 SQL 里到处都能出现、
tokenizer 又是无上下文的，于是那道闸能被 agent 用不到 1KB 的输入直接关掉（实测）：

- `CREATE INDEX iN ON dws.z (c);` × 20 当前缀（各贡献一个 ON、零个 JOIN），
  再接 20 张表的裸链 → 计数 0，闸不触发，parse 6.19s / 6.48s；
- 同一条语句尾巴上补 `ON`（`... WHERE x IN (1) ON ON ON …`，无上下文照数）→
  parse 6.79s，且这条 SQL 语法非法、parse 抛异常 → 返回 `[]`：**慢且全漏记**。

每多 2 张表耗时 ×8，n≈26 就是分钟级；`extract_tables` 在每次调用上都跑（含被守卫
拒掉的），所以工具那道只读守卫挡不住它。故计数按"这个 JOIN 自己后面紧跟着有没有
ON/USING"来算（见 `_unmatched_join_count`）。

而 `CROSS JOIN` / `NATURAL JOIN` 要**豁免**：它们定义上不带 ON，照数就把一整条合法
且快的链（实测 23 张 CROSS 0.003s、NATURAL 0.0003s）推进降级路径，白丢 CTE 名剔除、
让 CTE 名冒进审计。豁免按紧邻判定，不给别处的裸 JOIN 当掩护（否则又是一个绕过口）。

## 降级路径是**有损**的，得在生产日志里看得见

走降级就意味着这一行审计的 `tables` 精度是已知打了折的（CTE 名可能被记成表），且这种
输入大概率是攻击探测。所以那条日志是 **WARNING** 不是 DEBUG —— 生产跑在 INFO 级别
（`env/.env.prod.example`），DEBUG 那行在生产一次都不会打印。
"""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope
from sqlglot.tokens import TokenType

from app.core.logger import logger

# 方言与 join 编译同源，不另写一份（两处各写必然漂移）
from ..retrieval.dialect import sqlglot_dialect

# 没配对的 JOIN（JOIN 数减去 ON/USING 数）超过这个数就不进 parse。
# 8 远高于任何手写查询里"不带 ON 的 JOIN"的数量，又远低于 16 张表那个危险区。
_MAX_UNMATCHED_JOINS = 8

# FROM / JOIN 后面跟的标识符链由这些 token 组成（`dws`.`t1` 里 backtick 名是 IDENTIFIER）
_IDENT_TOKENS = frozenset({TokenType.VAR, TokenType.IDENTIFIER})

# 一个 JOIN 的 ON/USING 只能出现在它自己后面、下一个子句开始之前。走到这些 token
# 就说明 FROM 子句结束了，之前那个 JOIN 再没机会配到 ON —— 判它没配对。
_CLAUSE_ENDERS = frozenset({
    TokenType.WHERE, TokenType.GROUP_BY, TokenType.HAVING, TokenType.ORDER_BY,
    TokenType.LIMIT, TokenType.OFFSET, TokenType.QUALIFY, TokenType.WINDOW,
    TokenType.UNION, TokenType.INTERSECT, TokenType.EXCEPT,
    TokenType.SELECT, TokenType.INTO, TokenType.VALUES,
})

_JOIN_MATCHERS = frozenset({TokenType.ON, TokenType.USING})

# ★ CROSS JOIN / NATURAL JOIN **定义上**不带 ON —— 拿"没配对"去数它们等于把一整条
# 合法且快的链判成病理输入（实测 23 张 CROSS 链 parse 0.003s、NATURAL 链 0.0003s），
# 白白推进降级路径，代价是 CTE 名被记成表（正是 CTE 剔除要防的幻影表）。
_UNCONDITIONAL_JOIN_KINDS = frozenset({TokenType.CROSS, TokenType.NATURAL})

# JOIN 前面允许出现的修饰词。豁免必须按**紧邻**判定（CROSS/NATURAL 与 JOIN 之间只准
# 隔这些词，如 `NATURAL LEFT OUTER JOIN`），不能用一个粘住的标志位：那样文里任何一处
# 冒出的 CROSS/NATURAL token 都能给后面某个裸 JOIN 当掩护，又是一个 ON 式的绕过口。
_JOIN_QUALIFIERS = frozenset({
    TokenType.LEFT, TokenType.RIGHT, TokenType.FULL, TokenType.INNER,
    TokenType.OUTER, TokenType.SEMI, TokenType.ANTI,
}) | _UNCONDITIONAL_JOIN_KINDS


def _cte_reference_ids(statement: exp.Expression) -> set[int]:
    """这条语句里哪些 `exp.Table` 节点其实是 CTE 引用（按 id 定位具体节点）。

    用 `build_scope` 按作用域判定，而不是收集 CTE 名做字符串匹配 —— 只有当某个
    `Table` 节点所在的那个作用域里确实有同名 CTE，它才算 CTE 引用。

    返回节点 id 而非名字：调用方仍用 `find_all` 枚举（`build_scope` 会漏掉
    `INSERT INTO t SELECT ...` 的写入目标表，那也是"碰了哪张表"，不能丢），
    这里只负责标出该跳过的那几个节点。

    额外要求带库名/catalog 限定的一律不算 CTE 引用：CTE 名不可能带库名，而
    `scope.cte_sources` 是按裸名索引的，光比 `table.name` 会把 `dws.orders`
    当成 CTE `orders` 给吃掉（又一次漏记）。
    """
    root = build_scope(statement)
    if root is None:
        return set()
    return {
        id(table)
        for scope in root.traverse()
        for table in scope.tables
        if not table.args.get("db")
        and not table.args.get("catalog")
        and table.name in scope.cte_sources
    }


def _unmatched_join_count(tokens: list) -> int:
    """没配对的 JOIN 数，按**相邻**判定（一个 ON 只能抵掉它自己那个 JOIN）。

    这是 parse 会不会指数级回溯的判据。tokenize 是线性的（微秒级），本函数也只扫一遍，
    所以这道闸本身不会成为新的性能问题。

    ★ 不能写成 `JOIN 总数 - ON 总数`：那等于任意位置的一个 ON 都能抵掉一个裸 JOIN，
    而 `ON` 在 SQL 里到处都能出现（`CREATE INDEX x ON t`），tokenizer 又无上下文
    （尾巴上光秃秃的 `ON ON ON` 也照数）。实测两种不到 1KB 的输入都能把闸关掉、
    让 6s+ 的 parse 照跑（见模块 docstring）。所以只认"JOIN 后面紧跟的那个 ON"：

    - 每个 JOIN 把"待配对"置上；置的时候若已经有一个待配对的，说明上一个 JOIN 到这里
      都没等到 ON —— 记一笔没配对（`t0 JOIN t1 JOIN t2` 里 `JOIN t1` 就是这么记上的）。
    - `ON`/`USING` 只清掉当前那一个待配对，多余的 ON 不再有抵扣力。
    - 括号是独立作用域（子查询里的 JOIN/ON 不跨界互抵）；`;` 后是另一条语句，
      前一条挂着的待配对就地结算。
    - 走到 WHERE/GROUP BY/UNION 等子句边界时，还挂着的那个也判没配对。
    - ★ `CROSS JOIN` / `NATURAL JOIN` 不置待配对：它们**定义上**不带 ON，数进来等于
      把一条合法且快的链判成病理输入（见 `_UNCONDITIONAL_JOIN_KINDS`）。豁免按
      **紧邻**判定，别处冒出来的 CROSS/NATURAL 不给后面的裸 JOIN 当掩护 —— 否则这就是
      第三个 ON 式绕过口。它们仍会**结算**前面挂着的那个待配对（`t0 JOIN t1 CROSS
      JOIN t2` 里 `JOIN t1` 照记一笔）。
    """
    unmatched = 0
    # 每层括号一个"这层是否有个 JOIN 还在等 ON"的标记
    pending: list[bool] = [False]
    # 紧邻在 JOIN 前的那串修饰词里有没有 CROSS/NATURAL（`NATURAL LEFT OUTER JOIN`）
    qualified_unconditional = False
    for token in tokens:
        token_type = token.token_type
        if token_type in _JOIN_QUALIFIERS:
            qualified_unconditional = (
                qualified_unconditional or token_type in _UNCONDITIONAL_JOIN_KINDS
            )
            continue
        # 任何非修饰词都会打断这串修饰词 —— 豁免只对紧跟其后的那个 JOIN 有效
        is_unconditional_join = qualified_unconditional
        qualified_unconditional = False
        if token_type is TokenType.L_PAREN:
            pending.append(False)
        elif token_type is TokenType.R_PAREN:
            # 多余的右括号（病理输入里常见）不能把栈掏空
            if len(pending) > 1 and pending.pop():
                unmatched += 1
        elif token_type is TokenType.SEMICOLON:
            unmatched += sum(pending)
            pending = [False]
        elif token_type is TokenType.JOIN:
            # 前面挂着的那个照样结算（CROSS/NATURAL 也是个子句边界）
            if pending[-1]:
                unmatched += 1
            # CROSS/NATURAL JOIN 自己不等 ON，不置待配对
            pending[-1] = not is_unconditional_join
        elif token_type in _JOIN_MATCHERS:
            pending[-1] = False
        elif token_type in _CLAUSE_ENDERS:
            if pending[-1]:
                unmatched += 1
            pending[-1] = False
    # 语句结尾（含未闭合的括号层）还挂着的一律算没配对
    return unmatched + sum(pending)


def _scan_tables_linearly(tokens: list) -> list[str]:
    """线性 token 扫描把 FROM/JOIN 后面的标识符链捞出来（parse 走不通时的降级路径）。

    ★ 降级**绝不能返回空列表**：病理输入恰恰是最可能是攻击探测的那种，此时让审计声称
    "谁都没读过表"是最坏的失败方向 —— 那会让这道闸比它修的 bug 更糟。所以宁可用一个
    粗糙但线性的扫描把名字捞回来。

    代价：拿不到作用域，**CTE 名会被当成表记进去**。这是本模块明写的"宁可多记也不能
    漏记"允许的偏差。

    ★ 逗号连接的表也要收：`FROM dws.a, dws.b, dws.c JOIN dws.j0 …` 里裸 JOIN 触发降级，
    而只认"FROM 后面紧跟的那一个标识符链"会把 `dws.b`/`dws.c` 丢掉 —— 漏的是真读过的
    表，而一份看起来正常的表名列表没人会去怀疑。所以遇到逗号就接着往下收（别名要跳过：
    `dws.b AS y` 只能记 `dws.b`，把 `y` 记成表会在治理页上冒出个查无此表的名字）。

    ★★ **JOIN 后面同样要跟逗号链**，这不是"下一个 JOIN 的事"：逗号比 JOIN 结合得松，
    `JOIN dws.b, dws.c` 在 MySQL/StarRocks 里合法，`dws.c` 是和 `dws.b` 平级被读的表。
    只跟 FROM 的话，一条 222 字符的
    `FROM a JOIN b, SECRET_SALARY, SECRET_TOKENS JOIN pad0 … pad9`
    会让两张真表从审计里静默消失，而 `tables` 列看起来完全正常（parse 路本来就对，
    只有降级路径漏）。`CROSS JOIN d9, SECRET_X` 是同一个病根的另一支。

    ★ 逗号链必须**遇到非表 token 就停**：`USING (a, b)` / `GROUP BY a, b` /
    `IN (1, 2)` 里的逗号后面是列名或字面量。多记是允许的方向，但把 `col_x` 记成表
    会让这一列**失去可用性**（治理页上分不出哪个名字是真表），所以只跨"表位置上真能
    出现"的东西：标识符链、别名、`AS`。L_PAREN / 关键字 / 数字一概终止。
    """
    names: list[str] = []

    def _take_ident(pos: int) -> tuple[str, int]:
        """从 pos 起收一条标识符链 ident (DOT ident)*，返回(名字, 新位置)。"""
        parts: list[str] = []
        while pos < n and tokens[pos].token_type in _IDENT_TOKENS:
            parts.append(tokens[pos].text)
            pos += 1
            if pos < n and tokens[pos].token_type is TokenType.DOT:
                pos += 1
                continue
            break
        return ".".join(parts), pos

    def _remember(name: str) -> None:
        if name and name not in names:
            names.append(name)

    i = 0
    n = len(tokens)
    while i < n:
        if tokens[i].token_type not in (TokenType.FROM, TokenType.JOIN):
            i += 1
            continue

        name, i = _take_ident(i + 1)
        _remember(name)

        # 逗号链：跳过可能的别名（`t x` / `t AS x`），逗号后再收一条。
        # FROM 和 JOIN 一视同仁 —— 逗号在两处都是"再来一张同级的表"。
        while i < n:
            if tokens[i].token_type in (TokenType.ALIAS, *_IDENT_TOKENS):
                i += 1
                continue
            if tokens[i].token_type is TokenType.COMMA:
                name, i = _take_ident(i + 1)
                _remember(name)
                continue
            # 其它任何 token（USING/GROUP BY/WHERE/L_PAREN/数字…）都终止逗号链，
            # 否则会把列名和字面量收成"表"
            break
    return names


def extract_tables(sql: str) -> list[str]:
    """SQL 里被读的表名，去重且保留最外层优先（广度优先）的出现顺序。

    解析失败返回**已收集到的部分**（永不抛，非字符串入参与 parse 阶段失败返回 `[]`）：
    单个节点渲染不出来只跳过它自己，绝不清空已经拿到的表名。

    排除 CTE 名不彻底：`build_scope` 返回 None 时（如 `EXPLAIN WITH ...`）CTE 名仍会
    被记成表 —— 多记，刻意接受。

    ★ **病理输入走降级路径**：没配对的 JOIN 超过 `_MAX_UNMATCHED_JOINS` 时跳过
    `sqlglot.parse`（那里在裸 JOIN 链上是指数级回溯，23 张表要 50s，卡死而不抛，
    try/except 挡不住），改用线性 token 扫描取表名。降级路径**丢掉 CTE 名剔除**，
    于是 CTE 名可能被记成表 —— 属"宁可多记也不能漏记"允许的偏差，走到时打一条
    **warning**（生产是 INFO 级别，debug 在那儿等于没打；而"这行审计精度降级了"
    和"这大概是攻击探测"两件事都得看得见）。

    保留顺序而非排序：第一张通常是主表，顺序本身带信息。`find_all` 是广度优先，
    所以最外层的表排在嵌套子查询里的表前面 —— 正合"第一张通常是主表"这个用意。
    """
    if not isinstance(sql, str) or not sql.strip():
        return []

    # ★ 这道闸放在 extract_tables 自己身上，不放在 record / Task 4 的边界上：
    # 本函数是公开 helper，别的调用点也会用；而它自己的契约就承诺"永不让调用方失败"，
    # 卡死是这个承诺目前唯一真正被破掉的方式，得在源头堵。
    try:
        tokens = sqlglot.tokenize(sql, dialect=sqlglot_dialect())
    except Exception as e:  # noqa: BLE001 - tokenize 也不得让审计失败
        logger.debug(f"裸 SQL 分词失败(不影响审计记录): {e}")
        tokens = []
    if tokens and _unmatched_join_count(tokens) > _MAX_UNMATCHED_JOINS:
        names = _scan_tables_linearly(tokens)
        # ★ WARNING 而不是 DEBUG：生产日志级别是 INFO（env/.env.prod.example），DEBUG 这行
        # 在生产**根本不打印**，而它报的两件事都要人看见 —— (1) 这种输入大概率是攻击探测；
        # (2) 这一行审计的 tables 是**已知降级**的准确度（CTE 名可能被记成表）。只在病理
        # 输入上触发，不会吵。上面 tokenize 失败和下面单节点渲染失败那两条留在 DEBUG：
        # 那两个是真的日常噪音。
        logger.warning(
            f"裸 SQL 未配对 JOIN 过多，跳过 sqlglot.parse 走线性扫描"
            f"(CTE 名可能被记成表): 取到 {len(names)} 张表"
        )
        return names

    # ★ `seen` 在 try 外面：出错时要能把**已经收集到的**表名交出去，而不是清空。
    # 丢掉已收集的名字与"CTE 名当黑名单把真实表抹掉"是同一个失败方向 —— 审计声称
    # 谁都没读过表。所以下面是两层降级：单个节点渲染失败只跳过它自己（内层 try），
    # 整体出错（parse / find_all / build_scope）也只返回手上已有的（外层 try）。
    seen: list[str] = []

    # ★ 整个函数体都在 guard 里，不只是 parse：遍历和 `.sql()` 生成同样会抛
    # （sqlglot 的 generator 遇到渲染不了的节点会抛 UnsupportedError / ValueError，
    # build_scope 也会抛），而 docstring 承诺的是无条件"永不抛"。
    try:
        statements = sqlglot.parse(sql, dialect=sqlglot_dialect())

        for stmt in statements:
            if stmt is None:
                continue
            # ★ skip_ids 里装的是 id()，只在 stmt 这棵树活着期间有意义（id 会随对象
            # 回收被复用）。这里 stmt 由本函数持有、生命周期覆盖整个内层循环，所以成立。
            # 若日后重构成"先算 ids、后换一棵树遍历"，这个跳过会静默失效（CTE 名重新
            # 冒进审计），届时必须改为传节点本身。
            skip_ids = _cte_reference_ids(stmt)
            for table in stmt.find_all(exp.Table):
                if id(table) in skip_ids:
                    continue
                try:
                    # ★ 别名必须剥掉：实测 `dws.t1 y` 的 sql() 返回 'dws.t1 AS y'，
                    # 直接用会让同一张表因别名不同而被记成两个"表"（自关联时尤其明显）。
                    # ★ this 必须 copy()：exp.Table(this=...) 会把原节点的孩子挂到这个
                    # 临时节点上（原树的 parent 指针从此指向一个用完就扔的对象）。现在
                    # 树是本地 parse 的、随即丢弃，没影响；但若日后改成接收外部已解析的
                    # 表达式，就会悄悄改坏调用方的树。
                    name = exp.Table(
                        this=table.this.copy(),
                        db=table.args.get("db"),
                        catalog=table.args.get("catalog"),
                    ).sql(dialect=sqlglot_dialect())
                except Exception as e:  # noqa: BLE001 - 一个节点渲染不出来不该连坐其他表
                    logger.debug(f"裸 SQL 单张表名渲染失败(其余表名照常记录): {e}")
                    continue
                if not name or name in seen:
                    continue
                seen.append(name)
        return seen
    except Exception as e:  # noqa: BLE001 - 审计不得因解析失败而丢记录
        logger.debug(f"裸 SQL 表名解析失败(不影响审计记录): {e}")
        # 返回 seen 而不是 []：parse 阶段就失败时 seen 本来就是空的（契约不变），
        # 但多语句里第二条挂了，第一条已解析出的表名要留住。
        return seen
