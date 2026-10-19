"""JOIN 可行性判定与 fan-out 拦截（设计 §五）。

## 为什么必须拦在编译前

dev 真实数仓实测（2026-07-01 ~ 07-07，经 `order_revenue → iap_bill_detail`）：

| | 单表 | join 后 |
|---|---|---|
| `SUM(final_revenue)` | 524,147.88 | **628,096.97**（+19.8%） |
| `COUNT(DISTINCT order_id)` | 39,621 | 39,621（不变） |

两条 SQL 都 `ok=True`、都过 EXPLAIN、**没有任何一层报错**。EXPLAIN 只验语法与
可执行性，不验"这个数字对不对"。所以一旦 SQL 被拼出来，就再没有地方能发现问题 ——
判定只能发生在 plan 阶段，字符串还没生成的时候。

## 三档处置

| 边（**定向到主实体侧之后**） | 聚合形态 | 处置 |
|---|---|---|
| `many_to_one` / `one_to_one`（dev 6/7 条） | 任意 | 直接 join |
| `one_to_many` / `many_to_many` / **取值不可信** | 会放大 或 判不了 | **拒绝** |
| 同上 | 不放大（`COUNT(DISTINCT …)`） | 允许 + 警告 |

## 为什么拦截在这里而不在 compiler

compiler 只该管"把 plan 变成字符串"。判"这条边会不会毁掉这个聚合"要同时看
`relationship` + 聚合形态 + `additivity`，那是 plan 阶段的决策。这样 compiler
保持零 DB、纯字符串，可被穷举单测。

## 关系是**有向**的：反向登记必须把关系一起反向

`sem_join.relationship_type` 的语义是 `from_entity : to_entity`（见 `join/model.py`
的列注释，快照编译见 `model/service.py` 的 `join_json`）。可达性判定必须双向 ——
边的登记方向不该影响"能不能关联"；但**基数判定不能双向**：

`iap_bill_detail -many_to_one-> order_revenue`（多个账单明细对一个订单）与
`order_revenue -one_to_many-> iap_bill_detail` 是同一条边的两种登记写法。若只看
字面的 `relationship`，从 `order_revenue` 出发就会读到 `many_to_one` → 判成安全边
→ 放行 `SUM` → 得到那个虚增 19.8% 的数字，且全链路无一处报错。

故本模块先把关系按主实体侧**重新定向**（`_orient`），再做判定，并把这一步写进
`warnings` 留痕 —— 它是一步非显然的推断，且直接决定放行还是拒绝，必须可审。

## 白名单开在"安全"那一侧，不开在"危险"那一侧

判据是「关系∈{many_to_one, one_to_one} 才直通」，**不是**「关系≠one_to_many 就直通」。
快照是 JSON，`relationship` 可能来自旧版快照、手改、拼错、大小写不一致。若白名单
开在危险侧，一个拼错的枚举值就能让 `SUM` 直通那条虚增 19.8% 的边 —— 而拼错这件事
本身不会有任何人发现。取值不可信时按"可能放大"处理，并在 `allowed` 里指出去修登记。

## `on_expr` / `join_type` 会直接进 SQL 字符串，必须先验

- `on_expr` 为空 → 下游若照拼是语法错，若"识别到空就省略 ON"正好退化成 **CROSS
  JOIN**（笛卡尔积）—— 本模块另一条规则专门在防这个，不能从这里漏出去。
- `join_type` 只允许 `inner` / `left`（与 `sem_join` 的校验一致）。`RIGHT JOIN`
  会补出主实体侧不存在的行、`CROSS JOIN` 就是笛卡尔积，两者都改变聚合结果且不报错。
  非字符串取值（`7` / `[]`）同样拒 —— `_norm` 会把它收敛成 `""` 从而静默落到
  `left`，落点虽安全但与 `on_expr` 的处置不对称：读代码的人无法判断"快照里
  join_type=7"是被拦下了还是被当成了 left。快照里出现非字符串就是登记坏了。

## `on_expr` 的**内容**也必须验，不只是"填了没填"

只验存在性会留下第二个 fan-out 洞。实测（本轮复现）：

| 边 | `on_expr` | 旧实现 | 后果 |
|---|---|---|---|
| `order_revenue -many_to_one-> dim_user` | `1=1` | **JoinPlan** | 恒真 ON = 笛卡尔积 |
| 同上 | `order_revenue.x = totally_other.y` | **JoinPlan** | 关联到第三张表或报错 |

恒真 ON 的放大方式与那条把 `SUM(final_revenue)` 从 524,147.88 抬到 628,096.97
（+19.8%）的一对多边**完全一样**，而此时登记的 `many_to_one` 是句谎话 —— 上面
整套基数判定全被绕过，而且和那次一样，SQL 与 EXPLAIN 都不会报错。

本模块的立论正是"快照是可手改的 JSON，字段不可信"（见上面白名单那一节），
`relationship` 按这个标准被怀疑了，`on_expr` 没有理由例外。compiler 抓不到 ——
它的职责只是"把 plan 变成字符串"；所以这道闸只能开在这里，否则就没人开。

判据（`_on_expr_problem`）：ON 里必须**至少有一个顶层 AND 链上的等值条件把这两个
实体连起来**，且 ON 里不能出现这两个实体之外的第三张表、不能有子查询、不能有 SQL
注释。宽松侧刻意留了余地 —— dev 7 条真实边里有 2 条形如
`CAST(order_revenue.order_date AS CHAR) = dim_date.date`，要求"裸列 = 裸列"会把
`dim_date` 这条合法边一并封掉，那是另一种故障。故只看等值两侧**各自涉及哪些实体**，
容忍 CAST/函数包裹、反引号限定符与复合键 AND。反过来 `OR` / `NOT` 之下的等值不算数：
`a.x = b.y OR 1=1` 整体仍是恒真，`NOT (a.x = b.y)` 整体是关联条件取反。

第二轮复审又补上两类（都已实测复现旧实现返回 JoinPlan）：

| `on_expr` | 旧实现 | 为什么要拒 |
|---|---|---|
| `order_revenue.user_id = dim_user.user_id -- AND 1=1` | **JoinPlan**（`on_expr` 原样带注释） | 卫生 + plan 可读性 + 未来内联发出的风险 |
| `… AND EXISTS (SELECT 1 FROM evil_t)` | **JoinPlan** | 子查询里的第三张表对"只扫列前缀"的越界检查是隐形的 |

注释那一条的严重性要说准：compiler 现在把 ON 单独放一行发出，尾部 `--` 只吃掉那
一行的剩余部分，实测 `WHERE` / `GROUP BY` / `LIMIT` 都还在；未闭合的 `/*` 则让整条
语句 `TokenError`。**它今天不会静默出错数**，拒它是因为 (a) JOIN 条件里放注释永远
不是合法登记，(b) 带注释时"眼睛读 `plan.on_expr`"与"校验看到的 AST"不是同一个东西，
可审性破口，(c) 一旦将来 ON 改成内联发出，`--` 立刻能吃掉后面全部条件。

子查询那一条是过滤而非放大，数字被静默**缩小**而非虚增 —— 但缩小同样无法核对，且
本闸自己的判据就是"无法确认它关联的是这两个实体"。详见 `_on_expr_problem` docstring。

## 端点比较只用一套规则（规范化）

`_find_edges` 曾按原样比较端点、`_orient` 按规范化比较，两套规则会串味：边登记成
`ORDER_REVENUE → order_revenue` 时可达性能匹配上，而定向认为"from 就是主实体"→
**不镜像关系** → 拿到一个基数判定静默失效的 JoinPlan。既然 `relationship` 的
大小写要收敛（快照手改），端点名没有理由不收敛。全部走 `_norm`。

## 为什么不做子查询预聚合改写

用"先在明细侧 GROUP BY 再 join"救 `ADDITIVE` 场景，要求系统自行决定
"按哪个键预聚合、聚合成什么" —— 那是**新的口径决策**，必须人审。编译器自行
决定等于绕过审核体系，也违背"SQL 不生成、查表得到"的立论。

## 为什么多条边不自动选一条

`sem_join` 没有 `confidence` 字段，无法像某些实现那样取最高分；而那种
**静默选一条不告警**正是缺陷。多条边一律拒绝并列出候选，让人去消歧。

## `additivity` 只作辅证

dev 实测该字段 8/29 为 `None`，且 `order_cnt` 是 `COUNT(DISTINCT order_id)` 却被
标成 `'additive'`。以它为主判据会无谓拒绝该指标。主判据是 `agg_shape` 解析出的
聚合形态；两者冲突时取保守侧，并把"疑似标错"写进 `warnings`（放行路径）或
`message`（拒绝路径）—— 拒绝路径也要留痕，否则治理侧永远看不到这个标错。
"""

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from .agg_shape import is_amplified_by_join
from .dialect import sqlglot_dialect

# 不放大行数的关系（**已按主实体侧定向之后**）。白名单开在安全侧：
# 不在这个集合里的取值一律按"可能放大"处理，含拼错、大写、旧快照、None。
_SAFE_RELATIONSHIPS = frozenset({"many_to_one", "one_to_one"})

# 反向登记时关系的镜像。`many_to_many` / `one_to_one` 自反，无需登记。
_REVERSED = {"one_to_many": "many_to_one", "many_to_one": "one_to_many"}

# 与 `sem_join` 的 `_JOIN_TYPES` 校验一致（join/schema.py）
_ALLOWED_JOIN_TYPES = frozenset({"inner", "left"})
_DEFAULT_JOIN_TYPE = "left"  # 与 sem_join.join_type 的 default 一致

# 给人看的关系名 —— 拒绝理由要让业务方看懂问题在哪
_REL_LABELS = {
    "one_to_many": "一对多",
    "many_to_many": "多对多",
    "many_to_one": "多对一",
    "one_to_one": "一对一",
}

# SQL 注释标记。必须在**解析之前**按原文扫 —— sqlglot 解析时把注释挪进
# `comments` 属性，`find_all` 看不到它，等值判据于是对着一个"被清洗过"的 AST
# 做判断，而 `plan.on_expr` 出去的是带注释的原文（见 `_on_expr_problem` 第 0 条）。
_COMMENT_MARKERS = ("--", "/*", "*/", "#")


@dataclass(frozen=True)
class JoinPlan:
    """一跳 join 的执行计划。`frozen` —— 这是决策结果，compiler 不该改它。

    `relationship` 是**定向到主实体侧之后**的值，不一定等于快照里的字面值。

    注：`frozen=True` 只挡字段重绑定，不深冻 `warnings` 这个 list —— 下游仍能
    `plan.warnings.append(...)`。这里保持 `list` 是接口约定（计划里定死、Task 5
    直接消费），不可变性靠约定而非语言强制。
    """

    to_entity: str
    on_expr: str
    join_type: str
    relationship: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Refusal:
    """拒绝，带可行动的理由。形态与既有 12 条校验规则的 `errors[]` 一致。

    `allowed` 写的是"你**能**做什么"，不是把失败换个说法再讲一遍 —— agent 拿它
    反思重试，人拿它知道该找谁改什么。
    """

    rule: str
    message: str
    allowed: list[str] = field(default_factory=list)


def _norm(value: object) -> str:
    """快照字段规范化：非字符串、空白、大小写都收敛掉。

    快照是 JSON，字段可能是 `None`、带空格、大写。若不规范化，`" many_to_one "`
    会落到"取值不可信"分支被误拒，`"MANY_TO_ONE"` 同理 —— 而这两个都是安全边。
    """
    return value.strip().lower() if isinstance(value, str) else ""


def _find_edges(main_entity: str, target_entity: str, joins: list[dict]) -> list[dict]:
    """两个方向都算 —— 边的**登记方向**不该影响可达性。

    注意这只解决"能不能关联"。基数判定必须另外定向，见 `_orient`。

    ★ 端点必须按 `_norm` 规范化后再比，与 `_orient` 用同一套规则。曾经这里按原样
    比、`_orient` 按规范化比，两套规则串味的后果是：边登记成
    `ORDER_REVENUE → order_revenue` 时可达性匹配得上（原样比出来恰好等于 pair），
    定向却认为"from 就是主实体"→ 关系不镜像 → 产出一个基数判定静默失效的
    JoinPlan。既然 `relationship` 的大小写要收敛（快照可手改），端点名同理。
    """
    pair = {_norm(main_entity), _norm(target_entity)}
    return [
        j for j in joins
        if {_norm(j.get("from_entity")), _norm(j.get("to_entity"))} == pair
    ]


def _orient(edge: dict, main_entity: str) -> tuple[str, bool]:
    """把 `relationship` 定向到「从 `main_entity` 看过去」，返回 `(关系, 是否反向)`。

    ★ 这是本模块最承重的一步。`relationship` 的语义是 `from_entity : to_entity`；
    当边以 `target → main` 的方向登记时，字面值描述的是**反方向**的基数，直接拿来
    判就会把 fan-out 边读成安全边（实测那条虚增 19.8% 的边正好有两种登记写法）。

    未知取值原样返回（不镜像）—— 它本来就要落到"取值不可信"分支，镜像它没有意义
    且会掩盖原始值。
    """
    raw = _norm(edge.get("relationship"))
    if _norm(edge.get("from_entity")) == _norm(main_entity):
        return raw, False
    return _REVERSED.get(raw, raw), True


def _one_hop_targets(main_entity: str, joins: list[dict]) -> list[str]:
    """从主实体出发一跳可达的实体，去重且稳定有序。

    这是 `multi_hop_unsupported` 的 `allowed` —— 必须只列**真的能走到**的实体。
    把整张 `joins` 倒出来（含与主实体无关的边）等于给错指引：agent 会照着去
    试一条根本走不通的路。

    端点比较同样走 `_norm`（与 `_find_edges` / `_orient` 一致）：若这里按原样比，
    一条 `ORDER_REVENUE → dim_x` 的边就不会出现在"可行动方案"里，而 `_find_edges`
    却能匹配到它 —— 同一张图两套答案。返回值给的是快照里的原始写法，人要照它去
    库里找那条登记。
    """
    main = _norm(main_entity)
    out: list[str] = []
    for j in joins:
        for near, far in ((j.get("from_entity"), j.get("to_entity")),
                          (j.get("to_entity"), j.get("from_entity"))):
            if _norm(near) == main and far and _norm(far) != main and far not in out:
                out.append(far)
    return out


def _on_expr_problem(on_expr: str, main_entity: str, target_entity: str) -> str | None:
    """ON 条件是否**真的**把这两个实体关联起来。返回问题描述，`None` 表示通过。

    ★ 这是第二道 fan-out 闸。只验"填了没填"时实测可复现：
    `relationship='many_to_one'` + `on_expr='1=1'` → 返回 JoinPlan，拼出来是笛卡尔积，
    放大方式与那条把 `SUM(final_revenue)` 从 524,147.88 抬到 628,096.97（+19.8%）
    的一对多边一模一样；`on_expr='order_revenue.x = totally_other.y'` 同样放行。
    两例都不会被 SQL 语法或 EXPLAIN 拦住 —— 与那次虚增一样，全链路无一处报错。

    判据（刻意宽松，避免过度拒绝这种反向故障）：

    0. 原文里不能有 SQL 注释标记（`--` / `#` / `/*` / `*/`）—— 必须在解析**之前**
       按原文扫，理由见下面"为什么注释要单独一条"。
    1. 能解析 —— 解析不出来就判不了它关联的是什么，按"一切不确定都拒绝"拒。
    2. ON 里不能出现这两个实体之外的第三张表 —— 既扫限定列前缀（`表.列`），也扫
       `exp.Table` 节点本身。越界引用要么下游报未定义别名，要么在 FROM 里凑巧有
       那张表时默默关联到别处，后者出的数没人能发现错。
    3. ON 里不能出现任何子查询（`exp.Select`）—— 见下面"为什么子查询一律拒"。
    4. 至少一个**顶层 AND 链上**的等值条件，其两侧分别只涉及这两个实体 ——
       即 `{左侧实体集, 右侧实体集} == {main, target}`。只看"两侧各自涉及哪些
       实体"而不要求"裸列 = 裸列"：dev 7 条真实边里有 2 条形如
       `CAST(order_revenue.order_date AS CHAR) = dim_date.date`，严格形状会把
       `dim_date` 这条合法边一并封掉。复合键（多个 AND）与常量过滤都放过。

    第 4 条只沿顶层 AND 走，不用 `find_all(EQ)` 全树扫：`a.x = b.y OR 1=1` 整体
    仍是恒真、`NOT (a.x = b.y)` 整体是关联条件取反，全树扫都会看到那个"合法"等值
    就放行。OR / NOT 之下的等值不构成任何关联保证。（这条决策由测试里的
    `OR 1=1` 与 `NOT (...)` 两个 case 钉住 —— 把 `_conjuncts` 换成全树扫会变红。）

    ## 为什么注释要单独一条（第 0 条），且必须扫原文

    实测（本轮复现）：`on_expr='order_revenue.user_id = dim_user.user_id -- AND 1=1'`
    旧实现返回 **JoinPlan**，`plan.on_expr` 原样带着 `-- AND 1=1`。因为 sqlglot
    解析时把注释挪到节点的 `comments` 属性里，`find_all(exp.Column)` 与等值判据
    看到的是一个**干净的**等值条件，于是判"通过"。`/* */` 与 `#` 同理。

    ★ 严重性要说准，不要跟着直觉夸大。我实测过下游：compiler 把 ON 单独放一行
    发出，所以一个尾部 `--` 只注释掉那一行的剩余部分，`WHERE` / `GROUP BY` /
    `LIMIT` 全都还在；而**未闭合**的 `/*` 会让整条语句 `TokenError`，是解析失败
    而不是执行出错的语义。**所以这不是今天就会静默出错数的洞。**

    拒它的理由是另外三条，每条独立成立：

    1. JOIN 条件里出现注释，在任何情况下都不是合法登记 —— 它只可能来自手改
       `sem_join.on_expr` 时的复制粘贴残留或被注释掉的旧条件，那本身就是"登记坏了"。
    2. `plan.on_expr` 是给人审的字段。带注释时**眼睛读到的**与 **AST 判定的**不是
       同一个东西 —— 审计者看到 `-- AND 1=1` 无法判断这个 plan 到底按什么条件放行，
       本模块整套"决策可审"的立论就在这里破了口。
    3. 这道闸是把一个未来的洞提前堵上：一旦有人把 ON 改成内联发出（与 `JOIN … ON`
       同一行），尾部 `--` 立刻就能吃掉后面的全部内容。届时没有人会想到回来加这道
       检查 —— 而现在加它的成本是零。

    扫原文而非扫 AST，是因为解析后注释已经不在等值判据的视野里；这条检查的目的正是
    "原文与 AST 不一致"，只能在原文上做。

    ## 为什么子查询一律拒（第 3 条），以及越界扫描为什么要加 `exp.Table`

    实测（本轮复现）：下面两条在真实边上都返回 **JoinPlan**：

    - `order_revenue.a = dim_user.b AND EXISTS (SELECT 1 FROM evil_t)`
    - `order_revenue.a = dim_user.b AND order_revenue.c IN (SELECT k FROM evil_t)`

    因为越界检查只扫**限定列前缀**（`表.列`），而 `evil_t` 只作为子查询的 FROM
    出现，一次都没被写成 `evil_t.<列>` —— 对旧扫描是隐形的。

    这两例是**过滤**而非放大，所以数字被静默**缩小**而不是虚增。但缩小的数字和
    虚增的数字一样无法核对，而且本闸自己写下的判据就是"无法确认它关联的是这两个
    实体"，子查询正好落在这句话里：它能自带 FROM、自带过滤，第 4 条的等值判据对它
    完全无效。合法关联条件不需要子查询（dev 7 条真实边 0 条用到），拒它没有过度
    拒绝的风险。

    对应地把 `exp.Table` 的表名一起纳入越界扫描 —— 这样"第三张表出现在 ON 的任何
    位置"都会被点名，不再依赖它是否以 `表.列` 的形式被引用。
    """
    # 0. 注释标记：必须在解析前按原文扫（解析会把注释移出等值判据的视野）
    found_markers = [m for m in _COMMENT_MARKERS if m in on_expr]
    if found_markers:
        return (
            f"ON 条件里出现了 SQL 注释标记[{'、'.join(found_markers)}]，"
            f"JOIN 条件中不允许注释 —— 注释会被解析器静默吃掉，"
            f"使得校验看到的条件与登记的原文不是同一个东西：{on_expr}"
        )

    try:
        tree = sqlglot.parse_one(on_expr, dialect=sqlglot_dialect(), into=exp.Condition)
    except Exception:  # 解析失败是预期分支（快照是手写 JSON），不抛
        return f"ON 条件无法解析为合法的关联条件：{on_expr}"

    pair = {_norm(main_entity), _norm(target_entity)}

    # 2. 越界引用：ON 里出现了这两个实体之外的表。限定列前缀与 exp.Table 都要扫 ——
    #    子查询的 `FROM evil_t` 不会以 `evil_t.<列>` 的形式出现，只扫列会漏。
    referenced = {col.table for col in tree.find_all(exp.Column) if col.table}
    referenced |= {t.name for t in tree.find_all(exp.Table) if t.name}
    outsiders = sorted({r for r in referenced if _norm(r) not in pair})
    # ★ 库/目录限定也算越界：`other_db.dim_user.user_id` 的 `col.table` 仍是 `dim_user`，
    #   只比表名会把它当成本实体放过 —— 而它指向的是**另一个库的同名表**，
    #   声明的实体与真实读取的物理表不一致，正是本函数要挡的"指向别处"。
    #   实体自己的物理库由快照的 physical 三元组决定，不该在 ON 里另行限定。
    qualified = sorted({
        f"{node.db}.{node.name if isinstance(node, exp.Table) else node.table}"
        for node in [*tree.find_all(exp.Column), *tree.find_all(exp.Table)]
        if getattr(node, "db", None)
    })
    if not outsiders and qualified:
        return (
            f"ON 条件里出现了库限定[{'、'.join(qualified)}]："
            f"实体的物理库由快照决定，ON 里另行限定可能指向另一个库的同名表"
        )
    if outsiders:
        return (
            f"ON 条件引用了这两个实体之外的表[{'、'.join(outsiders)}]，"
            f"无法确认它关联的是[{main_entity}]与[{target_entity}]"
        )

    # 3. 子查询：能自带 FROM、自带过滤，下面的等值判据对它完全无效 → 判不了 → 拒
    if any(tree.find_all(exp.Select)):
        return (
            f"ON 条件里含子查询（SELECT），无法确认它关联的是[{main_entity}]与"
            f"[{target_entity}] —— 子查询可自带 FROM 与过滤条件，"
            f"会静默改变结果行集：{on_expr}"
        )

    # 4. 顶层 AND 链上必须有一个把两侧实体连起来的等值条件
    def _conjuncts(node: exp.Expression):
        """展开顶层 AND（含括号），OR/NOT 之下的不算。"""
        while isinstance(node, exp.Paren):
            node = node.this
        if isinstance(node, exp.And):
            yield from _conjuncts(node.this)
            yield from _conjuncts(node.expression)
        else:
            yield node

    def _entities(node: exp.Expression) -> set[str]:
        return {_norm(c.table) for c in node.find_all(exp.Column) if c.table}

    for part in _conjuncts(tree):
        if not isinstance(part, exp.EQ):
            continue
        left, right = _entities(part.this), _entities(part.expression)
        # 一侧一个实体、两侧合起来正好是这一对 —— 这才叫"把它们关联起来"
        if len(left) == 1 and len(right) == 1 and left | right == pair:
            return None

    return (
        f"ON 条件里没有把[{main_entity}]与[{target_entity}]关联起来的等值条件："
        f"{on_expr} —— 恒真或缺关联键的 ON 拼出来就是笛卡尔积"
    )


def plan_join(
    *,
    main_entity: str,
    target_entity: str,
    joins: list[dict],
    agg_expr: str | None,
    additivity: str | None,
) -> JoinPlan | Refusal:
    """判能否按一跳边把 `target_entity` join 进来。

    `joins` 取自 Release 快照的 `joins[]`（`{from_entity,to_entity,on_expr,
    relationship,join_type}`），纯 `list[dict]` 入参 —— 本模块零 DB 依赖，可穷举单测。

    `agg_expr` 为 `None` 或 `agg_shape` 判不了时，在可能放大的边上按**拒绝**处理：
    把"判不了"当成"不会放大"就是把风险默认放行。
    """
    # 端点一律按 `_norm` 比（与 `_find_edges` / `_orient` 同一套规则）：
    # `main='order_revenue'` + `target='ORDER_REVENUE'` 是同一个实体，若按原样比
    # 会往下走进"找边"，而那条自反边定向出来的关系是假的。
    if _norm(main_entity) == _norm(target_entity):
        return Refusal(
            rule="no_join_needed",
            message=f"维度已在主实体[{main_entity}]上，无需 join（调用方判断有误）",
            allowed=[f"直接在 {main_entity} 上按该维度拆分，不要走跨实体关联"],
        )

    edges = _find_edges(main_entity, target_entity, joins)
    if not edges:
        return Refusal(
            rule="multi_hop_unsupported",
            message=(
                f"实体[{main_entity}]与[{target_entity}]之间没有直接的 JOIN 关系，"
                f"本功能只支持一跳关联（拒绝生成 CROSS JOIN 以避免笛卡尔积）"
            ),
            # 只列真的一跳可达的实体，不是把整张 joins 倒出来
            allowed=_one_hop_targets(main_entity, joins),
        )
    if len(edges) > 1:
        return Refusal(
            rule="ambiguous_join",
            message=(
                f"实体[{main_entity}]与[{target_entity}]之间有 {len(edges)} 条 JOIN 关系，"
                f"无法确定用哪一条 —— 请让数据负责人保留唯一一条，或明确指定"
            ),
            allowed=[str(j.get("on_expr") or "(未填 on_expr)") for j in edges],
        )

    edge = edges[0]
    relationship, reversed_edge = _orient(edge, main_entity)
    warnings: list[str] = []

    # ---- 先验会直接进 SQL 字符串的两个字段 ----
    on_expr = (edge.get("on_expr") or "").strip() if isinstance(edge.get("on_expr"), str) else ""
    if not on_expr:
        return Refusal(
            rule="join_on_missing",
            message=(
                f"边[{main_entity}↔{target_entity}]没有 ON 条件，无法安全关联 —— "
                f"缺 ON 的 JOIN 等于笛卡尔积，会让结果成倍虚增"
            ),
            allowed=["请数据负责人补全该 JOIN 关系的 on_expr（sem_join）"],
        )

    # 存在性之外还要验内容：恒真 ON / 越界引用 ON 会绕过下面整套基数判定。
    # ★ 必须在基数判定**之前** —— 实测那个组合是"登记 many_to_one + on_expr=1=1"，
    #   基数判定会说"安全"，只有这里能拦下。
    # 尾注只写对**所有** join_on_invalid 分支都成立的那句话。曾经这里无条件贴
    # "实测恒真 ON 会让 SUM 虚增 19.8%"，但注释类问题实测并不会出错数（compiler
    # 把 ON 单独放一行，尾部 `--` 只吃掉那一行的剩余部分），把它说成虚增是夸大 ——
    # 拒绝理由夸大和漏报一样有害：读的人下次就不信这些数字了。
    on_problem = _on_expr_problem(on_expr, main_entity, target_entity)
    if on_problem:
        return Refusal(
            rule="join_on_invalid",
            message=(
                f"边[{main_entity}↔{target_entity}]的 ON 条件不可信：{on_problem}"
                f"（ON 会直接进 SQL 字符串，此类登记问题 SQL 与 EXPLAIN 都不会报错；"
                f"其中恒真 ON 实测会让 SUM 从 524,147.88 虚增到 628,096.97）"
            ),
            allowed=[
                f"请数据负责人把该 JOIN 关系的 on_expr 改成恰好关联"
                f"[{main_entity}]与[{target_entity}]的等值条件"
                f"（形如 {main_entity}.<键> = {target_entity}.<键>，允许两侧套 CAST/函数）",
            ],
        )

    raw_join_type = edge.get("join_type")
    # 非字符串（`7` / `[]`）不能靠 `_norm` 收敛成 "" 再静默落到 left ——
    # 落点安全但与 on_expr 的处置不对称，且掩盖了"快照登记坏了"这件事
    if raw_join_type is not None and not isinstance(raw_join_type, str):
        join_type = ""
    else:
        join_type = _norm(raw_join_type) or _DEFAULT_JOIN_TYPE
    if join_type not in _ALLOWED_JOIN_TYPES:
        return Refusal(
            rule="join_type_unsupported",
            message=(
                f"边[{main_entity}↔{target_entity}]的 join_type=[{edge.get('join_type')}]"
                f"不受支持：RIGHT/FULL 会补出主实体侧不存在的行、CROSS 就是笛卡尔积，"
                f"两者都会改变聚合结果且不报错"
            ),
            allowed=[
                f"请数据负责人把该 JOIN 关系的 join_type 改为 {sorted(_ALLOWED_JOIN_TYPES)} 之一",
            ],
        )

    if reversed_edge:
        # 反向定向是一步非显然的推断，且它直接决定放行/拒绝 —— 必须留痕可审。
        # 注意这条警告只随 JoinPlan 出去；走到 Refusal 时 warnings 丢掉了，但拒绝
        # 理由里带的是**定向后**的关系名（如"一对多"），与快照字面值可能不一致。
        # 这是有意的：读拒绝理由的人要知道"实际上是一对多"，而不是"登记里写的是什么"。
        warnings.append(
            f"该边在语义模型里以[{target_entity}→{main_entity}]方向登记，"
            f"已按主实体侧反向定向为[{_REL_LABELS.get(relationship, relationship or '未知')}]"
        )

    # ---- 基数判定：安全白名单之外的一律走保守侧 ----
    if relationship not in _SAFE_RELATIONSHIPS:
        # 主判据是聚合形态；additivity 只作辅证（实测该字段 8/29 为 None 且有标错）
        amplified = is_amplified_by_join(agg_expr) if agg_expr else None
        rel_label = _REL_LABELS.get(relationship)
        # 未登记/拼错的取值不能装作知道它是什么，但也不能因此放行
        rel_desc = (
            f"{rel_label}关系" if rel_label
            else f"关系登记为[{edge.get('relationship')}]（不是已知取值，按可能放大处理）"
        )

        if amplified is not False:
            # True（会放大）与 None（判不了）都拒绝 —— 保守侧。
            # ★ 必须写 `is not False`：写成 `if amplified:` 会把全部 None 放行，
            #   那正是本模块要堵的洞。
            reason = (
                "该聚合经此关联会重复计数" if amplified
                else "无法判定该聚合是否会被重复计数（聚合形态解析不出唯一收敛聚合）"
            )
            message = (
                f"维度在实体[{target_entity}]上，而它与[{main_entity}]是"
                f"{rel_desc} —— {reason}"
                f"（实测此类关联会让 SUM 从 524,147.88 变成 628,096.97，虚增 19.8%，"
                f"且 SQL 与 EXPLAIN 均不报错）"
            )
            # additivity 标错在拒绝路径上也要留痕，否则治理侧永远看不到 ——
            # Refusal 没有 warnings 字段，只能并进 message
            if amplified and additivity == "non_additive":
                message += (
                    "；另：该指标 additivity 标为 non_additive，但聚合形态是可加聚合，"
                    "疑似标错，请治理侧核对"
                )
            allowed = [f"改用 {target_entity} 侧的指标"]
            if rel_label:
                allowed.append(
                    f"请数据负责人在 {main_entity} 上补一条按该维度拆分的口径"
                )
            else:
                # 关系压根没登记对，第一件事是去把它修好，而不是让人绕路
                allowed.append(
                    "请数据负责人核对该 JOIN 关系的 relationship 登记"
                    "（合法取值：many_to_one / one_to_many / one_to_one）"
                )
            return Refusal(rule="fanout_refused", message=message, allowed=allowed)

        # amplified is False：COUNT(DISTINCT …) 对行数放大免疫，放行但必须提示
        warnings.append(
            f"经{rel_desc}[{main_entity}→{target_entity}]取数；"
            f"本指标为去重计数，结果不受行数放大影响"
        )
        if additivity == "additive":
            warnings.append(
                "指标 additivity 标为 additive 但聚合形态是去重计数 —— "
                "疑似标错，请治理侧核对"
            )

    return JoinPlan(
        to_entity=target_entity,
        on_expr=on_expr,
        join_type=join_type.upper(),
        relationship=relationship,
        warnings=warnings,
    )
