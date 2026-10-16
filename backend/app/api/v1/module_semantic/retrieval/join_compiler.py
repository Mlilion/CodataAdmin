"""两表 SQL 编译（设计 §六）。

## 边界

本模块**只把 `JoinPlan` 变成字符串**，不做任何业务判断 —— fan-out 是否安全、
边是否唯一，都已在 `join_planner` 决策完毕。保持零 DB、纯字符串，可穷举单测。

"不做业务判断"不等于"不做正确性检查"。本模块**唯一**的判断是「我能不能忠实地
把这份 plan 变成一条语义等价的 SQL」；答不了就抛 `JoinCompileError`，绝不产出
半成品。这条线划在这里的原因：编译之后再没有任何一层能发现问题 ——
`join_planner` 的模块注释已经论证过 EXPLAIN 只验语法与可执行性、不验"数字对不对"。

## 别名约定

`t0` = 主实体，`t1` = join 进来的实体。只做一跳，不产生 `t2+`。

## 每个成分的来源（没有一处由 LLM 决定）

| 成分 | 来源 |
|---|---|
| 聚合 | `indicator.measure_expression` 或从口径 SQL 反解 |
| 口径条件 | `caliber_parts.split_where` 反解所得 |
| 主表 / join 表 | 实体的 `physical` 三元组 |
| ON 条件 | `sem_join.on_expr`，别名重写 |
| 分组列 | 维度绑定（`bindings[].field`） |
| 时间条件 | 实体 `default_time_field` + 请求时间窗 |

## ★ 每个 WHERE 条件都必须自带括号（最贵的一处）

`caliber_parts.split_where` 明确写着「纯口径谓词整条原样保留，内部有 OR 也无妨」，
所以口径条件里**会**出现顶层 `OR`。用裸 `AND` 拼接时实测产出：

    WHERE t0.order_date BETWEEN 'a' AND 'b'
      AND t0.order_type = 'sub' OR t0.order_type = 'trial'

SQL 里 `AND` 绑得比 `OR` 紧，这条等价于 `(时间 AND 订阅) OR 试用` ——
**时间窗对 `trial` 那一支彻底失效**，全历史的试用订单被混进来。语法没错、
EXPLAIN 通过、数字照样返回。它与"丢掉一条口径条件"是同一类故障（口径静默变宽），
只是更隐蔽：条件明明在 SQL 文本里看得见。故每条一律加括号。

## ★ 别名重写走 AST，不走正则

正则 `\\b(\\w+)\\.(\\w+)` 有三处实测缺陷，每一处都会毁掉一条合法边或一个常量：

| `on_expr` | 正则的结果 | 后果 |
|---|---|---|
| `` `order_revenue`.`order_id` = `iap_bill_detail`.`order_id` `` | **一个字都没改** | 反引号不是 `\\w`；SQL 报"表不存在"，而 `join_planner` 明说容忍反引号 |
| `ORDER_REVENUE.order_id = IAP_BILL_DETAIL.order_id` | 一处都没改 | `join_planner` 全程用 `_norm`（小写）比端点，大小写不一致是能过闸的合法登记 |
| `… AND dim_user.tag = 'order_revenue.x'` | 常量变成 `'t0.x'` | 正则不认识字符串字面量，业务常量被悄悄改值 |

AST 改写只动 `exp.Column` 节点，三条全部消解。代价是 `on_expr` 会被重新序列化
（反引号可能被规范化），这没有语义影响 —— 而 Task 3 已经把注释/子查询/第三张表
/库限定全部拒在上游，所以这里能安全地假设它是一个干净的 Condition。

## ★ 表达式入参里不许残留 `{占位符}`（终审 Important 1）

`_qualify_strict` 此前不看占位符，于是 `agg_expr` 这条入参**从没被任何一层查过**
（pipeline 的占位符闸只扫口径条件）。实测：

    _qualify_expr_lenient("SUM(IF(region = '{region}', final_revenue, 0))", "t0")
    → SUM(CASE WHEN t0.region = '{region}' THEN t0.final_revenue ELSE 0 END)

那个 `{region}` 字面量原样进库：最好的结果是那一支恒为 0（"地区收入"静默变成 0），
最坏的结果是匹配到一个真叫 `{region}` 的值 —— 两种都不报错。同一个指标在填参路上
`bind_sql` 抛 `占位符缺少取值: ['region']`，所以这又是一处"join 路弱于它替代的填参路"。
`{start}` 落在聚合里（月累计口径的常见写法）更坏：CASE 恒假、**静默返回一个 0**，
连"0 行"这条线索都没有。

判据放在 `_qualify_strict` 里 —— 四个表达式入参（聚合 / 分组列 / 时间条件 / 口径条件）
全都从这一个函数过，所以第五个入参加进来时**自动**被覆盖，不必再打一次补丁。
正则复用 `sql_binder._PLACEHOLDER`：两条路对"什么算占位符"共用同一份定义，
才不会在 `sql_binder` 下次扩展占位符语法时又静默漂开（pipeline 侧同理）。

残余：`plan.on_expr` 不走 `_qualify_strict`（它由 `_rewrite_on_expr` 处理），
故本闸不覆盖它。那条路径的可信度由 `join_planner._on_expr_problem` 负责。

## ★ `_qualify_expr_lenient` 不下钻子查询

实测计划版：`order_id IN (SELECT id FROM dim_x)` →
`t0.order_id IN (SELECT t0.id FROM dim_x)`。子查询里的 `id` 被贴上外层别名，
`t0` 在那个作用域里不存在；更坏的情况是它变成一个**相关子查询** ——
过滤语义整体改变，且不报错。所以只给"属于当前作用域"的列加前缀。

代价是子查询内部既不加前缀、**也走不到别名校验**。所以 `compile_joined_sql` 的四个
表达式入参（聚合 / 分组列 / 时间条件 / 口径条件）一律**拒绝含子查询**
（`_reject_subquery`）—— 与 Task 3 对 `on_expr` 的立场同一条。理由见下一节。

## ★ ON 条件里的列必须全部限定（复审 Important 1）

Task 3 的 `_on_expr_problem` 只看"等值两侧各自涉及哪些实体"，不管额外的合取项：
实测 `_on_expr_problem('order_revenue.a = dim_user.b AND status = 1', ...)` 返回
`None`。编译后产出 `ON t0.a = t1.b AND status = 1` —— 只有一张表有 `status` 时
StarRocks **静默绑到那一侧**，join 的行集合被悄悄改掉。这与本模块为口径条件立的
规矩是同一类故障，只是从没应用到 ON 上。补前缀是业务判断（补错了不报错、只是数字
变了），故一律拒。

## ★ 主实体名与关联实体名不能只差大小写（复审 Important 2）

别名表以 `strip().lower()` 为键。两个实体名规范化后相同时两条键撞成一条，实测
`_rewrite_on_expr('order_revenue.a = ORDER_REVENUE.b', 'order_revenue',
'ORDER_REVENUE')` → `t1.a = t1.b`，一条不提 `t0` 的 ON = 笛卡尔积，且能过
`guard_readonly`。`plan_join` 拒绝自关联对所以正确调用方到不了这里 —— 但只在
"调用方正确"时成立的不变量必须自己钉住。

## 降级兜底只对聚合表达式成立，对条件必须抛

`_qualify_expr_lenient` 解析失败原样返回（DataSage `compiler.py:20-43` 的行为，
本模块沿用）。但**条件**原样发出是危险的：列没有 `t0.` 前缀时，两表都有该列
StarRocks 报 ambiguous（还算响），只有 `t1` 有时会**静默绑到 t1 的同名列** ——
过滤作用在了错的表上，数字变了没人知道。故 `compile_joined_sql` 在条件与聚合上
都改用严格模式（`_qualify_strict`），失败即抛，与 `caliber_parts`
「失败一律抛，绝不返回空」同一条立场。

★ 终审 Minor 3：宽松版原名 `qualify_expr`，是**公开的**（无下划线）。实测它在 app 侧
**零调用点**（`compile_joined_sql` 全程用 `_qualify_strict`），只有 9 处测试引用，
而它自己的 docstring 就在劝读者别用它 —— 一个"公开、无人调用、且自称危险"的名字是
在邀请下一个人误用（他会以为这是本模块的入口，然后拿到一条降级为原样返回的条件）。
故改名为 `_qualify_expr_lenient`：语义与用例一个不动（宽松兜底的行为本身有价值，
它是严格版的对照组，测试靠它证明"宽松会出什么事、所以严格版必须存在"），
只是把它从模块公开面上撤下来。任务书原本要求提供一个公开的 `qualify_expr`，
这一条被"不留无人调用的公开 API"取代。

## 标识符与表名一律引号化 / 重新序列化

别名来自快照里的指标/维度 code，表名来自 `physical` 三元组 —— 都是可手改的
JSON，本项目全程按不可信对待（见 `join_planner` 关于白名单开在安全侧的论证）。
裸插字符串时 `agg_alias='x FROM evil_t'` 能注入结构，而 `guard_readonly` 抓不到
它（没有写关键字）。引号化后注入片段降级成一个怪名字，不再改变语句结构。

## 契约：不改 plan，也不改聚合表达式

- `plan` 是 `frozen` 决策结果，本模块只读。特别是 `relationship`（已由 Task 3
  定向），不写回任何地方。
- 聚合表达式**只加列前缀，不反解、不改语义**。一对多边只在聚合是
  `COUNT(DISTINCT …)` 时才被放行，那次放行的安全性完全建立在"去重语义原样到达
  SQL"上；语义变了，fan-out 免疫就不成立 —— 而数字照样返回。测试里对 4 种
  `COUNT(DISTINCT …)` 形态钉住了 DISTINCT 的往返保真。

  ★ 但"不改"要说准（复审 Minor B）：加前缀要过一遍 sqlglot AST，**重新序列化时
  文本会被规范化**，所以到达 SQL 的字符串不保证与入参逐字节相同。实测：

  | 入参 | 产物 |
  |---|---|
  | `IF(a=1,b,NULL)` | `CASE WHEN a = 1 THEN b ELSE NULL END` |
  | `GROUP_CONCAT(DISTINCT a)` | `GROUP_CONCAT(DISTINCT a SEPARATOR ',')` |
  | `json_extract(payload,'$.x')` | `payload -> '$.x'` |

  这些都是**等价改写**（同一方言内的语法糖展开），语义与聚合基数不变，`DISTINCT`
  一个不少 —— fan-out 免疫依然成立。本模块保证的是"语义与 DISTINCT 保真"，
  不是"文本保真"。逐字节 diff `measure_expression` 与产物会看到差异，那是 AST
  路线的固有代价（换来的是下面那三条正则缺陷的消解）。

`_qualify_expr_lenient` 借鉴 DataSage `compiler.py:20-43`（该文件是其引擎里最可移植的
部分：零 DB 依赖）。
"""

import sqlglot
from sqlglot import exp

from .dialect import sqlglot_dialect
from .join_planner import JoinPlan

# ★ 复用 `sql_binder` 那一份编译好的占位符正则，不在本模块另写一个 —— 见模块注释
# 「表达式入参里不许残留 `{占位符}`」。join 路与填参路对"什么算占位符"必须共用同一份
# 定义，否则 `sql_binder` 下次扩展占位符语法时这里会静默落后（pipeline 侧同理，
# 它在自己的 import 处记了同一条理由）。
from .sql_binder import _PLACEHOLDER

_MAIN_ALIAS = "t0"
_JOIN_ALIAS = "t1"


class JoinCompileError(ValueError):
    """编译失败。抛而不是产出半成品 SQL —— 编译之后再没有一层能发现问题。

    与 `caliber_parts.CaliberParseError` 同一条立场：拒绝编译的代价只是该次请求
    退回填参路，产出一条坏 SQL 的代价是一个数字对不上、且无人能发现的报表。
    """


def _in_current_scope(node: exp.Expression, root: exp.Expression) -> bool:
    """`node` 是否属于 `root` 这一层作用域（中间没有隔着子查询的 SELECT）。

    子查询里的列不属于外层，给它加外层别名会让 `t0` 越界引用：轻则"未知别名"，
    重则变成相关子查询 —— 过滤语义整体改变且不报错。实测计划版
    `order_id IN (SELECT id FROM dim_x)` → `SELECT t0.id FROM dim_x`。
    """
    cur = node.parent
    while cur is not None and cur is not root:
        if isinstance(cur, exp.Select):
            return False
        cur = cur.parent
    return True


def _reject_subquery(tree: exp.Expression, *, what: str) -> None:
    """表达式入参里出现子查询即抛 —— 它能把第三张表带进 SQL。

    ★ 复审 Important 3 实测：`agg_expr='SUM(x) + (SELECT SUM(y) FROM evil_t)'` 编译
    通过，`evil_t` 就落在 SELECT 列表里；`group_column='(SELECT max(x) FROM evil_t)'`
    同时污染 SELECT 与 GROUP BY。`_table_sql` / `_alias_sql` 之所以要加固，理由是
    "表名/别名来自可手改的快照 JSON" —— `agg_expr` / `group_column` / `time_condition`
    / 口径条件来自**同一份**快照，此前一处没设防，与
    `test_table_injection_is_neutralized` 的立意自相矛盾。

    ★ 顺带关掉 `_qualify_tree` 的一个盲区：它按设计"不下钻子查询"（否则 `t0.` 会越界
    引用），于是子查询内部既不加前缀、**也不走别名校验**。实测
    `time_condition="order_id IN (SELECT id FROM dim_x WHERE t1.flag = 1)"` 整条溜过去，
    那个 `t1.flag` 让它悄悄变成一个相关子查询 —— 过滤语义完全改变且不报错。

    与 Task 3 对 `on_expr` 的立场完全一致（那里也是"有子查询就拒"）：编译器无法核对
    一个额外作用域读了什么，只能拒。
    """
    if any(True for _ in tree.find_all(exp.Select)):
        raise JoinCompileError(
            f"{what}里含子查询（SELECT），无法编译：子查询能把第三张表带进 SQL，"
            f"且它自成一层作用域、本模块的别名校验够不到里面"
        )


def _qualify_tree(
    tree: exp.Expression, alias: str, *, strict_prefix: str | None = None,
) -> str:
    """给树里当前作用域的未限定列加 `alias.` 前缀，返回渲染后的 SQL。

    `strict_prefix` 非空时，当前作用域里**已限定**的列必须限定到 `alias` 本身，
    否则抛 —— 见 `_qualify_strict` 的 docstring。
    """
    wrong: set[str] = set()
    for col in tree.find_all(exp.Column):
        if not _in_current_scope(col, tree):
            continue
        if col.table:
            if strict_prefix and col.table.strip().lower() != alias.lower():
                wrong.add(col.table)
            continue
        col.set("table", exp.to_identifier(alias))
    if wrong:
        raise JoinCompileError(
            f"{strict_prefix}里的列限定到了{sorted(wrong)}，而这一段必须落在 "
            f"[{alias}] 上 —— 限定到另一个别名会让条件/维度静默作用在错的表上"
        )
    return tree.sql(dialect=sqlglot_dialect())


def _reject_placeholder(text: str, *, what: str) -> None:
    """表达式里残留 `{占位符}` 即抛（终审 Important 1）。

    这道闸开在 `_qualify_strict` 里，而 `compile_joined_sql` 的**全部四个**表达式入参
    （聚合 / 分组列 / 时间条件 / 口径条件）都从那一个函数过 —— 所以第五个入参加进来时
    自动被覆盖，不必再补一次。上一版只有 pipeline 侧扫口径条件，`agg_expr` 免检。

    实测复现（`measure_expression="SUM(IF(region = '{region}', final_revenue, 0))"`）：

        SUM(CASE WHEN t0.region = '{region}' THEN t0.final_revenue ELSE 0 END)

    `{region}` 字面量原样进库：最好的结果是那一支恒为 0（"地区收入"静默变成 0），
    最坏的结果是匹配到一个真叫 `{region}` 的值。语法合法、`guard_readonly` 放行、
    EXPLAIN 通过、数字照样返回。而**同一个指标在填参路上是拒的**（`bind_sql` 抛
    `占位符缺少取值: ['region']`）—— "join 路弱于它替代的填参路"是本功能最不该出现的
    失败模式，这一条与占位符/标识符那两道闸同源。

    `{start}` 落在聚合里（月累计口径的常见写法）比 `{region}` 更坏：CASE 恒假，
    **静默返回一个 0** 而不是本意的值，连"0 行"这条线索都没有。

    正则复用 `sql_binder._PLACEHOLDER`（见 import 处）：两条路共用同一份定义，
    才不会各写一份然后悄悄漂开。
    """
    if leftover := sorted(set(_PLACEHOLDER.findall(text))):
        raise JoinCompileError(
            f"{what}里残留未填的占位符 {leftover}，无法编译：它会被原样拼进 SQL"
            f"（实测 SUM(IF(region = '{{region}}', …)) 产出 "
            f"SUM(CASE WHEN t0.region = '{{region}}' THEN … END)，"
            f"该分支恒为 0 或匹配到一个真叫这个名字的值，两种都不报错）"
        )


def _qualify_expr_lenient(expr: str, alias: str) -> str:
    """给表达式里未限定的列加 `alias.` 前缀（宽松版，**仅供测试作对照组**）。

    `SUM(final_revenue)` + `t0` → `SUM(t0.final_revenue)`
    已限定的（`t0.user_id`）跳过；`COUNT(*)` 无列不受影响；
    子查询内部的列不加前缀（它们不属于这一层作用域）。

    解析失败**原样返回** —— 降级兜底。注意 `compile_joined_sql` 内部**不用**这个宽松
    语义：条件原样发出会静默绑到 t1 的同名列，见模块注释「降级兜底只对聚合表达式成立」。

    ★ 终审 Minor 3：本函数原名 `qualify_expr`（公开），实测 app 侧 0 个调用点、
    9 处测试引用，且它自己的 docstring 在劝读者别用 —— 一个公开且自称危险的名字是在
    邀请误用。改为私有名，行为与用例一个不动（它是严格版的对照组：测试靠它证明
    "宽松会出什么事、所以严格版必须存在"）。
    """
    if not expr or not alias:
        return expr
    try:
        return _qualify_tree(sqlglot.parse_one(expr, dialect=sqlglot_dialect()), alias)
    except Exception:  # noqa: BLE001 - 降级为原样返回
        return expr


def _qualify_strict(expr: str, alias: str, *, what: str) -> str:
    """严格版：解析失败抛 `JoinCompileError`，不降级。

    ★ 用 `sqlglot.parse`（复数）而不是 `parse_one`：`parse_one` 只保留第一条语句、
    静默丢弃其余（`caliber_parts._parse` 与 `agg_shape._parse` 踩过同一个坑）。
    实测 `"order_type='x'; DROP TABLE t"` 被 `parse_one` 悄悄截成
    `order_type = 'x'` —— 截断本身不危险（危险的那半被丢了），但"我们收到的字符串
    与我们发出的不是同一个东西"这件事必须响，否则条件被截掉一半时同样静默。

    ★ 同时校验**已限定**的列指向的是不是本段该落的那个别名。实测漏掉这条会出现两种
    静默错表：`group_column='t0.bill_type'`（维度声明在 t1 上却按 t0 分组）与
    `caliber_conditions=['t1.order_type=...']`（口径过滤跑到 join 侧去了，`LEFT JOIN`
    下还会顺带把主表未匹配行过滤掉，等价于偷偷变成 INNER JOIN）。两者都语法合法、
    EXPLAIN 通过、数字照样返回 —— 与"丢掉口径条件"同一档故障，故一律抛。
    ★ 同时拒绝子查询（见 `_reject_subquery`）与残留占位符（见 `_reject_placeholder`）。
    宽松版 `_qualify_expr_lenient` 三条检查都不做（它的语义就是"能加就加"，
    且计划里有用例钉住它对子查询的行为）。

    ★ 占位符必须在**解析之前**查：`{region}` 落在字符串字面量里时 sqlglot 解析得动，
    等到解析完再看就要遍历字面量节点 —— 而"这条 SQL 文本里有没有占位符"本就是文本级
    判据，与填参路（`bind_sql` 也是文本级 `re.sub`）用同一层视角才不会漂。
    """
    text = (expr or "").strip()
    if not text:
        raise JoinCompileError(f"{what}为空，无法编译")
    _reject_placeholder(text, what=what)
    try:
        parsed = sqlglot.parse(text, dialect=sqlglot_dialect())
    except Exception as exc:
        raise JoinCompileError(f"{what}无法解析：{text}（{exc}）") from exc
    trees = [t for t in parsed if t is not None]
    if len(trees) != 1:
        raise JoinCompileError(
            f"{what}解析出 {len(trees)} 条语句（期望恰好 1 条）：{text}"
        )
    _reject_subquery(trees[0], what=what)
    return _qualify_tree(trees[0], alias, strict_prefix=what)


def _rewrite_on_expr(on_expr: str, main_entity: str, join_entity: str) -> str:
    """把 `on_expr` 里的实体名前缀换成 t0/t1。走 AST，不走正则。

    `sem_join.on_expr` 是人按实体名写的（`order_revenue.order_id = ...`），
    直接进 SQL 会报"表不存在"。

    端点按小写比 —— 与 `join_planner` 的 `_norm` 同一套规则。两边规则不一致时，
    一条 `ORDER_REVENUE.x = dim_user.y` 能过 planner 的闸却在这里一处都改不到，
    产出一条引用不存在表的 SQL（正则版实测如此）。

    出现第三个前缀就抛：计划版在推不出主实体名时把它原样留在 SQL 里，理由是
    "会在 EXPLAIN 阶段被发现"。但 EXPLAIN 只在真连库时才跑，而本模块的产物还会
    进日志、进"可复核 SQL"给人看 —— 一个同时出现 `t0` 与 `order_revenue` 的字符串
    会让复核的人以为这是三表查询。判不了就抛。

    ★ 复审 Important 2：别名表必须恰好两个不同的键。两个实体名只差大小写（或只差
    前后空白）时，`strip().lower()` 后两条键撞成一条，后写的赢。实测
    `_rewrite_on_expr('order_revenue.a = ORDER_REVENUE.b', 'order_revenue',
    'ORDER_REVENUE')` → `t1.a = t1.b` —— 一条**完全不提 `t0`** 的 ON，等于把
    LEFT JOIN 变成笛卡尔积；语法合法、`guard_readonly` 放行、数字照样返回。
    `plan_join` 拒绝自关联对，所以正确的调用方到不了这里，但本模块此前只校验了
    `main_entity` 是否为空 —— 一个只在"调用方正确"时才成立的不变量必须自己钉住。

    ★ 复审 Important 1：ON 里出现**未限定**的列就抛。实测
    `_on_expr_problem('order_revenue.a = dim_user.b AND status = 1', ...)` 返回
    `None` —— Task 3 确实放这种 ON 过闸（它只看"等值两侧各自涉及哪些实体"，不管
    额外合取项）。编译后产出 `ON t0.a = t1.b AND status = 1`：两表只有一张有
    `status` 时 StarRocks **静默绑到那一侧**，join 的行集合被悄悄改掉；两张都有时
    报 ambiguous。这与本模块 docstring 为口径条件立的规矩是同一类故障（"只有 t1 有
    时会静默绑到 t1 的同名列"），只是从没应用到 ON 上。

    这里刻意**不**像 `_qualify_strict` 那样"缺前缀就补一个" —— 补哪一侧是业务判断：
    补错了不报错，只是数字变了。编译器判不了就拒，与本模块的章程一致。
    """
    main_key = main_entity.strip().lower()
    join_key = join_entity.strip().lower()
    # 空实体名要单独拦：`{'order_revenue': 't0', '': 't1'}` 是**两个**键，
    # 逃得过下面的塌缩判断，而 ON 会被写成 `t0.a = t0.b`（同样是笛卡尔积）。
    # 上游 plan_join 不会产出空 to_entity、Task 3 也会先拒这种 on_expr，
    # 所以这是纵深防御 —— 但本模块是 Task 5 直接调用的入口，自己兜住更稳。
    if not join_key:
        raise JoinCompileError(
            f"关联实体名为空（主实体[{main_entity}]）—— ON 会被整条改写到 t0 上，"
            f"等于笛卡尔积，拒绝编译"
        )
    alias_map = {main_key: _MAIN_ALIAS, join_key: _JOIN_ALIAS}
    if len(alias_map) != 2:
        raise JoinCompileError(
            f"主实体[{main_entity}]与关联实体[{join_entity}]规范化后是同一个名字，"
            f"别名表塌成一条 —— ON 会被整条改写到 t1 上（实测 "
            f"'order_revenue.a = ORDER_REVENUE.b' → 't1.a = t1.b'，一条不提 t0 的 "
            f"ON 等于笛卡尔积），拒绝编译"
        )
    try:
        tree = sqlglot.parse_one(on_expr, dialect=sqlglot_dialect(), into=exp.Condition)
    except Exception as exc:
        raise JoinCompileError(f"ON 条件无法解析：{on_expr}（{exc}）") from exc

    unknown: set[str] = set()
    bare: set[str] = set()
    for col in tree.find_all(exp.Column):
        if not col.table:
            bare.add(col.name or col.sql(dialect=sqlglot_dialect()))
            continue
        alias = alias_map.get(col.table.strip().lower())
        if alias is None:
            unknown.add(col.table)
            continue
        col.set("table", exp.to_identifier(alias))
    if unknown:
        raise JoinCompileError(
            f"ON 条件里的前缀{sorted(unknown)}既不是主实体[{main_entity}]"
            f"也不是关联实体[{join_entity}]，无法改写成 t0/t1 别名：{on_expr}"
        )
    if bare:
        raise JoinCompileError(
            f"ON 条件里的列{sorted(bare)}没有限定实体前缀 —— 编译器无法判断它属于"
            f"主实体[{main_entity}]还是关联实体[{join_entity}]，猜错会静默改变 join "
            f"的行集合（只有一侧有该列时 StarRocks 直接绑到那一侧，不报错）。"
            f"请在快照里把 ON 条件的每个列都写成 实体名.列名：{on_expr}"
        )
    return tree.sql(dialect=sqlglot_dialect())


def _table_sql(table: str, *, what: str) -> str:
    """把 `db.table` 重新序列化成规范表名，顺手中和注入。

    表名来自快照 `physical` 三元组（可手改 JSON）。裸插时 `'dws.t, evil'` 会在 FROM
    里多出一张表，而 `guard_readonly` 抓不到它（没有写关键字）。重新序列化后它变成
    一个被引起来的表名，最坏结果是"表不存在"。
    """
    text = (table or "").strip()
    if not text:
        raise JoinCompileError(f"{what}为空，无法编译")
    try:
        return sqlglot.to_table(text, dialect=sqlglot_dialect()).sql(dialect=sqlglot_dialect())
    except Exception as exc:
        raise JoinCompileError(f"{what}不是合法的表名：{table}（{exc}）") from exc


def _alias_sql(alias: str, *, what: str) -> str:
    """输出别名走标识符渲染 —— 它来自快照里的 code，按不可信对待。

    实测 `agg_alias='x FROM evil_t'` 裸插时能注入结构；经 `to_identifier` 后它被
    引号包住，降级成一个怪列名。中文别名（维度名直接当别名很常见）同理 ——
    裸插是语法错。

    ★ 用 sqlglot 的**按需**引号化而不是 `quoted=True` 无条件加引号：正常别名
    （`order_cnt`）保持裸形，只有不安全的（含空格/关键字/非 ASCII/首字符是数字）
    才被引起来。既中和注入，又不给每个正常字段都套上反引号 —— 这个 SQL 是要给人
    复核的，满屏反引号会劣化可读性，而可读性正是本功能"决策可审"的一部分。
    实测覆盖：`x FROM evil_t`→引号、`城市`→引号、`order`（关键字）→引号、
    `order_cnt`→裸形。
    """
    text = (alias or "").strip()
    if not text:
        raise JoinCompileError(f"{what}为空，无法编译")
    return exp.to_identifier(text).sql(dialect=sqlglot_dialect())


def compile_joined_sql(
    *,
    agg_expr: str,
    agg_alias: str,
    main_entity: str,
    main_table: str,
    join_table: str,
    plan: JoinPlan,
    group_column: str,
    group_alias: str,
    caliber_conditions: list[str],
    time_condition: str,
    limit: int,
) -> str:
    """编译一跳两表聚合 SQL。

    `main_entity` 是主实体的**语义名**（不是表名），用来把 `on_expr` 里的实体前缀
    改写成 `t0`。★ 它是显式入参而不是从 `on_expr` 反推的：计划版
    `_main_entity_of(on_expr, to_entity)` 靠"扫前缀、取不等于 to_entity 的那个"来猜，
    在 `on_expr` 只写了一侧前缀（`order_revenue.a = b`）或前缀拼错时会猜出空串/错名，
    然后**静默**把主实体侧前缀原样留在 SQL 里。调用方（Task 5）本来就手握主实体名 ——
    传进来，不猜。

    `main_table` / `join_table` 为 `db.table` 形态。
    `caliber_conditions` 必须已由 `caliber_parts.split_where` 反解得到 ——
    少一条会让口径悄悄变宽；本函数保证每一条都出现在 WHERE 里且带 `t0.` 前缀，
    任何一条解析失败即抛 `JoinCompileError`（不静默跳过、不原样发出）。

    `agg_expr` 只加列前缀，**不重写不反解** —— 见模块注释里的契约那一节。
    """
    if not (main_entity or "").strip():
        raise JoinCompileError("主实体名为空，无法把 ON 条件改写成 t0/t1 别名")

    # LIMIT：非整数、0、负数都是调用方传错。`LIMIT 0` 返回空结果集，看起来像
    # "这个口径没数据"，是最难查的一类错；抛出来比产出一条坏 SQL 好。
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise JoinCompileError(f"limit 必须是正整数，收到 {limit!r}")
    if limit <= 0:
        raise JoinCompileError(f"limit 必须为正数，收到 {limit}")

    main_sql = _table_sql(main_table, what="主表")
    join_sql = _table_sql(join_table, what="join 表")

    # 维度表达式在 SELECT 与 GROUP BY 里各出现一次，必须**同一个字符串** ——
    # 两处各渲染一遍时任何渲染差异都会变成 StarRocks 的 "not in GROUP BY"。
    group_sql = _qualify_strict(group_column, _JOIN_ALIAS, what="分组列")
    agg_sql = _qualify_strict(agg_expr, _MAIN_ALIAS, what="聚合表达式")

    select_parts = [
        f"{group_sql} AS {_alias_sql(group_alias, what='分组列别名')}",
        f"{agg_sql} AS {_alias_sql(agg_alias, what='聚合别名')}",
    ]

    # ★ 每条都加括号。裸 AND 拼接时一条带顶层 OR 的口径会让时间窗对它的右支失效
    # （`(时间 AND A) OR B`），语法没错、EXPLAIN 通过、数字照样返回。
    where_parts: list[str] = []
    if (time_condition or "").strip():
        where_parts.append(
            f"({_qualify_strict(time_condition, _MAIN_ALIAS, what='时间条件')})"
        )
    for cond in caliber_conditions or []:
        # 空白条件不携带任何口径，忽略是安全的（且避免悬空 AND）；
        # 非空但解析不了的一律抛 —— 那是"有一条口径我处理不了"，不能静默丢。
        if not (cond or "").strip():
            continue
        where_parts.append(
            f"({_qualify_strict(cond, _MAIN_ALIAS, what='口径条件')})"
        )

    on_cond = _rewrite_on_expr(plan.on_expr, main_entity, plan.to_entity)

    sql_parts = [
        "SELECT",
        ",\n       ".join(select_parts),
        f"FROM {main_sql} AS {_MAIN_ALIAS}",
        # `join_type` 直接插值：Task 3 已把它白名单收敛到 {inner,left}（见
        # `join_planner._ALLOWED_JOIN_TYPES`），在这里再校一遍等于把同一道闸门
        # 跨模块复制两份，改一处漏一处比现在更危险。
        f"{plan.join_type} JOIN {join_sql} AS {_JOIN_ALIAS}",
        f"       ON {on_cond}",
    ]
    if where_parts:
        sql_parts.append("WHERE " + "\n  AND ".join(where_parts))
    sql_parts.append(f"GROUP BY {group_sql}")
    sql_parts.append(f"LIMIT {limit}")
    sql = "\n".join(sql_parts)

    # 自检：产物必须是恰好一条可解析的 SELECT。拼接期发现问题的成本是一个异常，
    # 执行期发现的成本是一次失败查询 + 一条没人能复核的日志。
    try:
        stmts = sqlglot.parse(sql, dialect=sqlglot_dialect())
    except Exception as exc:
        raise JoinCompileError(f"编译产物不是合法 SQL：{exc}\n{sql}") from exc
    if len(stmts) != 1 or not isinstance(stmts[0], exp.Select):
        raise JoinCompileError(f"编译产物不是单条 SELECT：\n{sql}")

    return sql
