"""JOIN 计划与 fan-out 拦截。

## 三档处置（设计 §五）

| 边 | 聚合形态 | 处置 |
|---|---|---|
| `many_to_one` / `one_to_one`（dev 6/7 条） | 任意 | 直接 join |
| `one_to_many`/`many_to_many`/**判不了** | 会放大 或 判不了 | **拒绝** |
| `one_to_many`/`many_to_many`/**判不了** | 不放大 | 允许 + 警告 |

## 为什么拦截在 planner 而不在 compiler

compiler 只该管"把 plan 变成字符串"，不该知道业务语义。判"这条边会不会毁掉
这个聚合"要同时看 `relationship` + 聚合形态 + `additivity`，那是 plan 阶段的决策。

## 本文件相对 brief 的偏离（均为实测/推演出的缺陷，见 report）

1. `relationship` 是**有向**的（`sem_join` 的语义是 from_entity : to_entity）。
   边反向登记时必须把关系一起反向，否则会把 fan-out 边读成安全边。
2. `relationship` 取值不在白名单内（拼错 / 大写 / 旧快照）时必须按保守侧处理，
   不能因为"它不等于 one_to_many"就直通。
3. `on_expr` 为空、`join_type` 不在 `{inner,left}` 内一律拒绝 —— 这两个值会直接
   进 SQL 字符串，空 ON 在下游可能退化成 CROSS JOIN（笛卡尔积）。
4. `on_expr` **只验在不验内容**同样是漏洞：`on_expr='1=1'` 在一条登记为
   `many_to_one` 的边上会被放行，拼出来就是笛卡尔积 —— 与那条虚增 19.8% 的边
   同样的 fan-out，而登记的"多对一"此时是句谎话。故还要验 ON 是否真的把这两个
   实体关联起来（见"ON 条件的内容"一节的测试）。
"""

from dataclasses import FrozenInstanceError

import pytest

from app.api.v1.module_semantic.retrieval.join_planner import (
    JoinPlan,
    Refusal,
    plan_join,
)

# dev 真实边（order_revenue 为主实体）
_JOINS = [
    {"from_entity": "order_revenue", "to_entity": "iap_bill_detail",
     "on_expr": "order_revenue.order_id = iap_bill_detail.order_id",
     "relationship": "one_to_many", "join_type": "left"},
    {"from_entity": "order_revenue", "to_entity": "dim_user",
     "on_expr": "order_revenue.user_id = dim_user.user_id",
     "relationship": "many_to_one", "join_type": "left"},
    {"from_entity": "iap_bill_detail", "to_entity": "dim_bill_type",
     "on_expr": "iap_bill_detail.bill_type = dim_bill_type.bill_type",
     "relationship": "many_to_one", "join_type": "left"},
]


def _edge(**over) -> list[dict]:
    """造一条 order_revenue→dim_user 的边，字段可覆写。"""
    base = {
        "from_entity": "order_revenue", "to_entity": "dim_user",
        "on_expr": "order_revenue.user_id = dim_user.user_id",
        "relationship": "many_to_one", "join_type": "left",
    }
    base.update(over)
    return [base]


# ---------------------------------------------------------------- many_to_one 直通


def test_many_to_one_is_allowed_for_additive_agg():
    """多对一不放大行数，SUM 也安全。dev 6/7 条边是这种。"""
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user", joins=_JOINS,
        agg_expr="SUM(final_revenue)", additivity="additive",
    )

    assert isinstance(plan, JoinPlan)
    assert plan.to_entity == "dim_user"
    assert plan.relationship == "many_to_one"
    assert plan.warnings == []


def test_one_to_one_is_allowed_for_additive_agg():
    """一对一同样不放大行数。`sem_join` 的第三个合法取值，brief 的实现里没测到。"""
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(relationship="one_to_one"),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )

    assert isinstance(plan, JoinPlan)
    assert plan.relationship == "one_to_one"
    assert plan.warnings == []


def test_join_type_is_upper_cased_for_sql_emission():
    """库里存小写（`inner`/`left`），出给 compiler 的是 SQL 关键字形态。"""
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(join_type="inner"),
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(plan, JoinPlan)
    assert plan.join_type == "INNER"


# ------------------------------------------------- 反向登记：可达性 + 关系必须一起反向


def test_edge_matches_in_reverse_direction():
    """边可能反向登记，两个方向都要能找到。

    ★ 与 brief 的差异：brief 这条测试用的数据是
    `dim_user -many_to_one-> order_revenue`，语义是"多个用户对一个订单"——
    那不是真实边的反向形态。真实边是 `order_revenue -many_to_one-> dim_user`，
    它的反向登记形态是 `dim_user -one_to_many-> order_revenue`（一个用户多个订单）。
    planner 必须把它按主实体侧重新定向回 `many_to_one`。
    """
    joins = [{"from_entity": "dim_user", "to_entity": "order_revenue",
              "on_expr": "dim_user.user_id = order_revenue.user_id",
              "relationship": "one_to_many", "join_type": "left"}]
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user", joins=joins,
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(plan, JoinPlan)
    # 定向到主实体侧：从 order_revenue 看过去是多对一
    assert plan.relationship == "many_to_one"
    # 反向定向是一步非显然的推断，且它直接决定放行/拒绝 —— 必须留痕可审
    assert any("反向" in w for w in plan.warnings)


def test_reverse_registered_fanout_edge_is_still_refused():
    """★★ brief 的病根：边反向登记时 `relationship` 没有一起反向。

    `iap_bill_detail -many_to_one-> order_revenue`（多个账单明细对一个订单）
    从 order_revenue 侧看就是一对多 —— 正是实测虚增 19.8% 的那条边，只是登记
    方向相反。brief 的实现会读到字面的 "many_to_one" 直接放行 SUM。
    """
    joins = [{"from_entity": "iap_bill_detail", "to_entity": "order_revenue",
              "on_expr": "iap_bill_detail.order_id = order_revenue.order_id",
              "relationship": "many_to_one", "join_type": "left"}]
    out = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=joins,
        agg_expr="SUM(final_revenue)", additivity="additive",
    )

    assert isinstance(out, Refusal)
    assert out.rule == "fanout_refused"


# ---------------------------------------------------------------- fan-out 拒绝


def test_one_to_many_with_additive_agg_is_refused():
    """★★ 实测虚增 19.8%（524,147.88 → 628,096.97）且不报错，必须拒。"""
    out = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=_JOINS,
        agg_expr="SUM(final_revenue)", additivity="additive",
    )

    assert isinstance(out, Refusal)
    assert out.rule == "fanout_refused"
    assert "一对多" in out.message
    # 拒绝必须给**可行动**的出路，不能只说"不行"。这里把内容也钉住：
    # 关系已知（一对多）时两条出路分别是"改用对侧指标"和"在主实体上补口径"，
    # 只断言 `out.allowed` 为真的话，实现退化成 `allowed=['不行']` 也能过。
    assert any(
        "iap_bill_detail" in a and "指标" in a for a in out.allowed
    ), f"要指出可以改用对侧实体的指标: {out.allowed}"
    assert any(
        "order_revenue" in a and "口径" in a for a in out.allowed
    ), f"要指出可以请人在主实体上补口径: {out.allowed}"


def test_many_to_many_with_additive_agg_is_refused():
    """多对多放大得更狠，同样拒。"""
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(relationship="many_to_many"),
        agg_expr="SUM(final_revenue)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "fanout_refused"
    assert "多对多" in out.message


def test_one_to_many_with_distinct_count_is_allowed_with_warning():
    """COUNT(DISTINCT) 实测不受影响（39,621 不变），放行但要提示。"""
    plan = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=_JOINS,
        agg_expr="COUNT(DISTINCT order_id)", additivity="non_additive",
    )

    assert isinstance(plan, JoinPlan)
    assert any("一对多" in w for w in plan.warnings)


def test_unknown_agg_shape_on_fanout_edge_is_refused():
    """★ 判不了聚合形态 + 一对多 = 拒绝（保守侧）。

    把"判不了"当成"不会放大"就是把风险默认放行。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=_JOINS,
        agg_expr=None, additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "fanout_refused"


@pytest.mark.parametrize("agg_expr", [
    "SUM(a) + SUM(b)",          # 两个聚合 → agg_shape 判不了
    "final_revenue",            # 没有聚合
    "not sql at all ((",        # 解析失败
    "SUM(x) OVER (PARTITION BY k)",  # 窗口聚合不收敛行数
    "",                         # 空串
    "SUM(DISTINCT x)",          # DISTINCT 挡不住求和
])
def test_undecidable_or_amplified_shapes_on_fanout_edge_are_refused(agg_expr):
    """凡是 agg_shape 不明确回答 False 的形态，一对多边上都拒。

    这条参数化是承重的：实现里必须写 `amplified is not False`，
    写成 `if amplified:` 就会把上面这些 `None` 全部放行。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=_JOINS,
        agg_expr=agg_expr, additivity=None,
    )
    assert isinstance(out, Refusal), f"{agg_expr!r} 不该被放行"
    assert out.rule == "fanout_refused"


def test_additivity_mislabel_does_not_override_shape():
    """★★ 实测 order_cnt 是 COUNT(DISTINCT order_id) 却标 'additive'。

    以形态为主判据 → 应放行；若信了字段就会无谓拒绝。
    """
    plan = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=_JOINS,
        agg_expr="COUNT(DISTINCT order_id)", additivity="additive",  # 字段标错
    )

    assert isinstance(plan, JoinPlan)
    assert any("additivity" in w for w in plan.warnings), "标错要提示治理侧修正"


def test_shape_says_amplified_but_field_says_non_additive_is_refused():
    """冲突取保守侧：形态说会放大 → 拒绝。"""
    out = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=_JOINS,
        agg_expr="SUM(final_revenue)", additivity="non_additive",  # 字段标错
    )
    assert isinstance(out, Refusal)


def test_conflicting_additivity_is_recorded_in_refusal_message():
    """★ 拒绝路径上也要留下标错线索 —— Refusal 没有 warnings 字段。

    形态说会放大、字段说 non_additive：拒绝是对的，但"字段标错了"这条治理信息
    在 brief 的实现里被丢掉了（只有放行路径才 append 警告）。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=_JOINS,
        agg_expr="SUM(final_revenue)", additivity="non_additive",
    )
    assert isinstance(out, Refusal)
    assert "additivity" in out.message


# --------------------------------------------------- relationship 取值不可信时的保守侧


@pytest.mark.parametrize("relationship", [
    "1:N", "ONE_TO_MANY_X", "", None, "unknown", "belongs_to",
])
def test_unrecognized_relationship_is_treated_as_fanout(relationship):
    """★★ brief 的第二个洞：用"是不是 one_to_many"做判据 = 白名单开在错的一侧。

    快照是 JSON，`relationship` 可能来自旧版快照 / 手改 / 拼错。任何不在
    `{many_to_one, one_to_one}` 里的取值都必须按"可能放大"处理，否则一个拼错
    的枚举值就能让 SUM 直通那条虚增 19.8% 的边。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(relationship=relationship),
        agg_expr="SUM(final_revenue)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "fanout_refused"
    # 要告诉治理侧去修登记，而不是只说"这个指标不能用"
    assert any("relationship" in a for a in out.allowed)


def test_unrecognized_relationship_still_allows_distinct_count():
    """判不了关系 + 去重计数 = 仍可放行：COUNT(DISTINCT) 对行数放大免疫。

    保守不等于一律拒绝 —— 只在"结果可能被改变"时拒。
    """
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(relationship="1:N"),
        agg_expr="COUNT(DISTINCT order_id)", additivity=None,
    )
    assert isinstance(plan, JoinPlan)
    assert plan.warnings


@pytest.mark.parametrize("raw", ["MANY_TO_ONE", " many_to_one ", "Many_To_One"])
def test_relationship_is_case_and_space_normalized(raw):
    """大小写/空格不该改变判定 —— 否则安全边被误拒、危险边被误放。"""
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(relationship=raw),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(plan, JoinPlan)
    assert plan.relationship == "many_to_one"
    assert plan.warnings == []


# ------------------------------------------------- 进 SQL 字符串的两个字段必须可信


@pytest.mark.parametrize("on_expr", ["", None, "   "])
def test_missing_on_expr_is_refused(on_expr):
    """★ 空 ON 条件必须拒 —— brief 把它兜成 `""` 交给 compiler。

    下游若照着拼就是 `LEFT JOIN t ON `（语法错），若识别到空就省略 ON，
    那正好退化成 CROSS JOIN（笛卡尔积）—— 本模块另一条规则专门在防这个。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr=on_expr),
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "join_on_missing"


@pytest.mark.parametrize("join_type", ["right", "full", "cross", "bogus"])
def test_unsupported_join_type_is_refused(join_type):
    """★ `sem_join` 只允许 inner/left；快照里出现别的一律拒。

    brief 直接 `.upper()` 放过去：`RIGHT JOIN` 到明细侧会补出主实体不存在的行，
    `CROSS JOIN` 就是笛卡尔积 —— 两者都会改变聚合结果且不报错。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(join_type=join_type),
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "join_type_unsupported"
    assert out.allowed


def test_missing_join_type_defaults_to_left():
    """缺省与库里的 default 一致（`sem_join.join_type` default='left'）。"""
    edge = _edge()
    del edge[0]["join_type"]
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user", joins=edge,
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(plan, JoinPlan)
    assert plan.join_type == "LEFT"


@pytest.mark.parametrize("join_type", [7, [], {}, 1.5, True, object()])
def test_non_string_join_type_is_refused_not_silently_defaulted(join_type):
    """★ 非字符串 join_type 不能静默落到默认值。

    `_norm` 把非字符串收敛成 `""`，`"" or 'left'` 就变成了 `left` —— 落点是安全的，
    但它与 `on_expr` 的处置不对称（非字符串 on_expr 直接拒），读代码的人无法判断
    "快照里 join_type=7" 到底是被拦下了还是被当成 left 了。快照里出现非字符串就是
    登记坏了，该让治理侧知道，而不是替它猜一个值。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(join_type=join_type),
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "join_type_unsupported"
    assert out.allowed


# --------------------------------------------- ON 条件的内容：必须真的关联这两个实体


@pytest.mark.parametrize("on_expr", [
    "1=1",                       # ★ 恒真：拼出来就是笛卡尔积
    "1 = 1",
    "TRUE",                      # 同上，另一种写法
    "order_revenue.user_id",     # 压根不是等值条件
    "order_revenue.dt >= dim_user.dt",  # 只有范围条件，没有等值关联键
    "user_id = user_id",         # 没有限定前缀，无法确认关联的是哪两张表
    # ★★ 下面两条把"只沿顶层 AND 链找等值"这个决策钉住（mutation-pinning）：
    # 把实现里的 `_conjuncts(tree)` 换成全树 `tree.find_all(exp.EQ)` 时，这两条
    # 会立刻变红 —— 因为全树扫能在 OR / NOT 之下看到那个"合法"的等值条件就放行，
    # 而整体语义分别是恒真（OR 1=1）与关联条件取反（NOT），都不构成关联保证。
    "order_revenue.user_id = dim_user.user_id OR 1=1",
    "NOT (order_revenue.user_id = dim_user.user_id)",
])
def test_on_expr_without_qualified_equality_between_the_pair_is_refused(on_expr):
    """★★ 只验 ON 存在、不验内容 = 第二个 fan-out 洞。

    实测：一条登记为 `many_to_one` 的边配 `on_expr='1=1'`，planner 返回 JoinPlan。
    恒真 ON 拼出来就是笛卡尔积，放大方式与那条把 SUM 从 524,147.88 抬到
    628,096.97（+19.8%）的一对多边一模一样 —— 而此时登记的"多对一"是句谎话，
    基数判定这一整套保护全被绕过。compiler 的职责只是"把 plan 变成字符串"，
    它抓不到；所以这道闸只能开在这里。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr=on_expr),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal), f"{on_expr!r} 不该被放行"
    assert out.rule == "join_on_invalid"
    # 可行动：得说清"要关联的是哪两个实体"，否则治理侧不知道该怎么改
    assert any("order_revenue" in a and "dim_user" in a for a in out.allowed)


@pytest.mark.parametrize("on_expr", [
    "order_revenue.user_id = totally_other.user_id",   # 引用了第三个实体
    "order_revenue.user_id = dim_user.user_id AND order_revenue.k = other.k",
    "iap_bill_detail.user_id = dim_user.user_id",      # 关联的压根不是主实体
])
def test_on_expr_referencing_a_third_entity_is_refused(on_expr):
    """★ ON 里出现这两个实体之外的限定前缀 → 拒。

    实测 `on_expr='order_revenue.x = totally_other.y'` 在真实边上返回 JoinPlan。
    这种 ON 编译出来要么是未定义别名（下游报错），要么在 FROM 里凑巧有那张表时
    默默关联到别处 —— 后者出的数没人能发现错。快照是手改得到的 JSON，这类越界
    引用必须当成登记坏了处理。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr=on_expr),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal), f"{on_expr!r} 不该被放行"
    assert out.rule == "join_on_invalid"


@pytest.mark.parametrize("on_expr", [
    "other_db.dim_user.user_id = order_revenue.a",
    "sem.order_revenue.user_id = other_db.dim_user.user_id",
])
def test_on_expr_with_database_qualifier_is_refused(on_expr):
    """★ 库/目录限定也算越界 —— 只比表名会把它当本实体放过。

    `other_db.dim_user.user_id` 的 `col.table` 仍是 `dim_user`，所以只扫表名的
    域外检查看不出问题；而它读的是**另一个库的同名表**，声明的实体与真实读取的
    物理表不一致 —— 正是本守卫要挡的"指向别处"，只是换了个维度。

    实体的物理库由快照的 `physical` 三元组决定，ON 里另行限定没有正当用途。
    实测修复前两例都返回 JoinPlan。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr=on_expr),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal), f"{on_expr!r} 不该被放行"
    assert out.rule == "join_on_invalid"


@pytest.mark.parametrize("on_expr", [
    # `--` 行注释：sqlglot 解析时把它当注释吃掉，AST 里只剩一个干净的等值条件
    "order_revenue.user_id = dim_user.user_id -- AND 1=1",
    # `#` 行注释（MySQL/StarRocks 方言）
    "order_revenue.user_id = dim_user.user_id # AND 1=1",
    # `/* */` 块注释
    "order_revenue.user_id = dim_user.user_id /* AND 1=1 */",
    "/* x */ order_revenue.user_id = dim_user.user_id",
    # 未闭合的块注释：sqlglot 直接 TokenError，本来就会被"解析不了"分支拒掉，
    # 但规则名必须一致（都是 join_on_invalid），这里一并钉住
    "order_revenue.user_id = dim_user.user_id /* AND 1=1",
])
def test_on_expr_containing_sql_comment_is_refused(on_expr):
    """★ ON 条件里出现 SQL 注释标记（`--` / `#` / `/* */`）→ 拒。

    实测（本轮复现）：`on_expr='order_revenue.user_id = dim_user.user_id -- AND 1=1'`
    旧实现返回 **JoinPlan**，且 `plan.on_expr` 原样保留了 `--` 之后的文本 ——
    因为 sqlglot 解析时静默把注释吃掉，`_on_expr_problem` 看到的是一个干净的等值
    条件，于是判定"通过"。`/* */` 与 `#` 同理。

    ★ 严重性要说准，不要夸大：compiler 现在把 ON 单独放一行发出，所以一个尾部
    `--` 只注释掉那一行的剩余部分，实测 `WHERE` / `GROUP BY` / `LIMIT` 都还在；
    未闭合的 `/*` 则让整条语句 TokenError 而不是执行出错的语义。**所以这不是
    今天就会出错数的洞。**

    拒它的理由是另外三条，每条都成立：
    1. JOIN 条件里放注释在任何情况下都不是合法登记，它只可能来自手改/复制粘贴残留；
    2. `plan.on_expr` 是给人审的字段，注释让"读 plan 就知道关联条件"这件事失效 ——
       眼睛看到的和 AST 判定的不是同一个东西；
    3. 一旦将来有人把 ON 改成内联发出（同一行），尾部 `--` 就立刻能吃掉后面的
       全部条件 —— 这道闸是把那个未来的洞提前堵上。

    复用既有规则 `join_on_invalid`（不新增规则名）。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr=on_expr),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal), f"{on_expr!r} 不该被放行"
    assert out.rule == "join_on_invalid"


@pytest.mark.parametrize("on_expr", [
    # ★ EXISTS 子查询里藏了第三张表 —— 旧实现返回 JoinPlan
    "order_revenue.a = dim_user.b AND EXISTS (SELECT 1 FROM evil_t)",
    # ★ IN 子查询同上
    "order_revenue.a = dim_user.b AND order_revenue.c IN (SELECT k FROM evil_t)",
    # 子查询里不带表也拒 —— 一个能自带 FROM/过滤的表达式无法被本模块判定
    "order_revenue.a = dim_user.b AND EXISTS (SELECT 1)",
    # 标量子查询
    "order_revenue.a = dim_user.b AND (SELECT 1) = 1",
])
def test_on_expr_containing_subquery_is_refused(on_expr):
    """★★ 越界检查只扫"限定列前缀"，子查询里 `FROM evil_t` 的第三张表是隐形的。

    实测（本轮复现）：上面前两条在真实边上都返回 **JoinPlan**。它们是过滤而非
    放大，所以数字是被**缩小**而不是虚增 —— 但一个被静默缩小的数字同样无法核对，
    而且本闸自己的判据就是"无法确认它关联的是这两个实体"，子查询正好落在这句话里。

    两处修复：
    1. ON 里出现任何 `SELECT`（`exp.Select`）一律拒 —— 合法关联条件不需要子查询，
       而子查询能自带 FROM、自带过滤，本模块的等值判据对它完全无效。
    2. 越界扫描把 `exp.Table` 的表名也算进去，这样"ON 里任何位置出现第三张表"
       都会被抓到，不再依赖它是否以 `表.列` 的形式出现。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr=on_expr),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal), f"{on_expr!r} 不该被放行"
    assert out.rule == "join_on_invalid"


def test_on_expr_naming_a_third_table_anywhere_is_refused():
    """★ 第三张表出现在 ON 里的任何位置（不只是列前缀）都要被越界扫描抓到。

    钉住"`outsiders` 要把 `exp.Table` 也算进来"这个实现细节：这里 `evil_t`
    只作为子查询的 FROM 出现，一次都没有以 `evil_t.<列>` 的形式被引用，旧实现的
    `find_all(exp.Column)` 扫描看不见它。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(
            on_expr="order_revenue.user_id = dim_user.user_id "
                    "AND dim_user.k IN (SELECT k FROM evil_t)",
        ),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal)
    assert out.rule == "join_on_invalid"
    assert "evil_t" in out.message, f"要点名那张越界的表: {out.message}"


@pytest.mark.parametrize("on_expr", [
    # dev 真实边（7/7 条都是这两种形态之一）
    "order_revenue.order_id = iap_bill_detail.order_id",
    "order_revenue.country = dim_country.country_name_en",
    # ★ 真实边里有一侧套了函数：CAST(...) —— 校验必须容忍函数/类型转换
    "CAST(order_revenue.order_date AS CHAR) = dim_date.date",
    "CAST(iap_bill_detail.bill_date AS CHAR) = dim_date.date",
])
def test_real_dev_on_expr_shapes_are_accepted(on_expr):
    """★ 防过度拒绝：dev 7 条真实边的两种 ON 形态都必须通得过。

    尤其是 `CAST(order_revenue.order_date AS CHAR) = dim_date.date` —— 要求
    "裸列 = 裸列" 的实现会把它拒掉，那就把 dim_date 这条合法边一并封了。
    """
    left, right = on_expr.split("=", 1)
    # 从表达式里把两端的实体名抠出来当端点，与真实边保持一致
    main = "order_revenue" if "order_revenue." in left else "iap_bill_detail"
    target = right.strip().split(".", 1)[0]
    plan = plan_join(
        main_entity=main, target_entity=target,
        joins=[{"from_entity": main, "to_entity": target, "on_expr": on_expr,
                "relationship": "many_to_one", "join_type": "left"}],
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(plan, JoinPlan), f"{on_expr!r} 被误拒了"
    assert plan.on_expr == on_expr


@pytest.mark.parametrize("on_expr", [
    # 多条等值条件（复合键）
    "order_revenue.user_id = dim_user.user_id AND order_revenue.dt = dim_user.dt",
    # 合法等值 + 一个纯常量过滤（常量不引入第三个实体，也不使 ON 恒真）
    "order_revenue.user_id = dim_user.user_id AND dim_user.is_valid = 1",
    # 反向书写：目标实体在左
    "dim_user.user_id = order_revenue.user_id",
    # 大小写与空格
    "ORDER_REVENUE.user_id = DIM_USER.user_id",
    # 两侧都套函数
    "lower(order_revenue.user_id) = lower(dim_user.user_id)",
    # 反引号限定符（StarRocks/MySQL 风格）—— 新增的注释/子查询闸不能把它扫成越界
    "`order_revenue`.`user_id` = `dim_user`.`user_id`",
])
def test_legitimate_on_expr_variants_are_accepted(on_expr):
    """合法但不"标准"的 ON 写法不能被误拒 —— 过度拒绝也是一种故障。"""
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr=on_expr),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(plan, JoinPlan), f"{on_expr!r} 被误拒了"


def test_unparseable_on_expr_is_refused():
    """ON 解析不出来 → 判不了它关联的是什么 → 拒（一切不确定都拒绝）。"""
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr="not sql at all (("),
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "join_on_invalid"


def test_on_expr_is_validated_before_cardinality_so_a_lying_edge_cannot_pass():
    """★ 恒真 ON + 登记 many_to_one：基数判定会说"安全"，ON 校验必须先拦下。

    这正是实测复现的组合：`relationship='many_to_one'` 让 fan-out 判定放行，
    而 `1=1` 让 SQL 变成笛卡尔积。两个检查各自都"通过"，只有 ON 内容校验能救。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user",
        joins=_edge(on_expr="1=1", relationship="many_to_one"),
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal)
    assert out.rule == "join_on_invalid"


# ------------------------------------------- 端点比较规则：两个 helper 必须用同一套


def test_case_variant_endpoints_are_matched_and_oriented_consistently():
    """★ `_find_edges` 按原样比、`_orient` 按规范化比 = 两套规则，会串味。

    边登记成 `ORDER_REVENUE → order_revenue`（大小写变体）时：可达性用原样比对
    能匹配上（`{'ORDER_REVENUE','order_revenue'}` 恰好等于 pair），而定向用规范化
    比对会认为"from 就是主实体"→ 不镜像关系。于是实测拿到了一个 relationship
    **没有被镜像**的 JoinPlan —— 定向修复静默失效，那正是虚增 19.8% 的成因。

    统一到规范化比较后：`ORDER_REVENUE` 与 `order_revenue` 规范化后是同一个实体，
    "把自己 join 自己"该走 `no_join_needed`，绝不该产出一个基数判定被绕过的计划。
    """
    joins = [{"from_entity": "ORDER_REVENUE", "to_entity": "order_revenue",
              "on_expr": "ORDER_REVENUE.k = order_revenue.k",
              "relationship": "many_to_one", "join_type": "left"}]
    out = plan_join(
        main_entity="order_revenue", target_entity="ORDER_REVENUE", joins=joins,
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal), "同一实体的大小写变体不该产出 JoinPlan"
    assert out.rule == "no_join_needed"


def test_case_variant_edge_endpoints_still_match_and_orient():
    """规范化比较的正面用例：端点大小写/空格不同也要能匹配 **且** 正确镜像关系。

    `iap_bill_detail -many_to_one-> order_revenue` 反向登记，端点写成大写。
    定向必须把它镜像成一对多并拒掉 SUM —— 否则又是那条虚增 19.8% 的边。
    """
    joins = [{"from_entity": " IAP_BILL_DETAIL ", "to_entity": "Order_Revenue",
              "on_expr": "iap_bill_detail.order_id = order_revenue.order_id",
              "relationship": "many_to_one", "join_type": "left"}]
    out = plan_join(
        main_entity="order_revenue", target_entity="iap_bill_detail", joins=joins,
        agg_expr="SUM(final_revenue)", additivity="additive",
    )
    assert isinstance(out, Refusal)
    assert out.rule == "fanout_refused"
    assert "一对多" in out.message


def test_case_variant_endpoints_are_counted_as_ambiguous_duplicates():
    """同一对实体、端点大小写不同的两条边是歧义，不能因写法不同就漏掉一条。"""
    joins = [
        {"from_entity": "order_revenue", "to_entity": "dim_user",
         "on_expr": "order_revenue.user_id = dim_user.user_id",
         "relationship": "many_to_one", "join_type": "left"},
        {"from_entity": "ORDER_REVENUE", "to_entity": " Dim_User ",
         "on_expr": "order_revenue.buyer_id = dim_user.user_id",
         "relationship": "many_to_one", "join_type": "left"},
    ]
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user", joins=joins,
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "ambiguous_join"


# ---------------------------------------------------------------- 其它拒绝路径


def test_no_edge_is_refused_not_cross_join():
    """无注册边 → 拒绝，绝不出 CROSS JOIN（笛卡尔积）。"""
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_bill_type", joins=_JOINS,
        agg_expr="SUM(x)", additivity=None,
    )

    assert isinstance(out, Refusal)
    assert out.rule == "multi_hop_unsupported"


def test_multi_hop_allowed_lists_only_one_hop_reachable_entities():
    """★ `allowed` 要写"你能做什么"，不是把全部边倒出来。

    brief 列的是 `joins` 全表，于是 `iap_bill_detail→dim_bill_type` 也进了
    "可行动方案"——从 order_revenue 出发根本走不到那里，等于给错指引。
    """
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_bill_type", joins=_JOINS,
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert set(out.allowed) == {"iap_bill_detail", "dim_user"}


def test_empty_joins_is_refused():
    """一条边都没有（模型未登记关系）→ 拒绝，且不能崩。"""
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user", joins=[],
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "multi_hop_unsupported"
    assert out.allowed == []


def test_ambiguous_multiple_edges_is_refused():
    """同对实体多条边 → 拒绝并列候选。

    ★ 我们没有 confidence 字段, 不能像 DataSage 那样静默取最高分那条 ——
    静默选边正是它的缺陷。
    """
    joins = _JOINS + [
        {"from_entity": "order_revenue", "to_entity": "dim_user",
         "on_expr": "order_revenue.buyer_id = dim_user.user_id",
         "relationship": "many_to_one", "join_type": "left"},
    ]
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user", joins=joins,
        agg_expr="SUM(x)", additivity=None,
    )

    assert isinstance(out, Refusal)
    assert out.rule == "ambiguous_join"
    assert len(out.allowed) == 2


def test_ambiguous_counts_reverse_registered_duplicate():
    """一条正向 + 一条反向登记的同对实体边，同样是歧义，不能因方向不同就当两回事。"""
    joins = [
        {"from_entity": "order_revenue", "to_entity": "dim_user",
         "on_expr": "order_revenue.user_id = dim_user.user_id",
         "relationship": "many_to_one", "join_type": "left"},
        {"from_entity": "dim_user", "to_entity": "order_revenue",
         "on_expr": "dim_user.user_id = order_revenue.buyer_id",
         "relationship": "one_to_many", "join_type": "left"},
    ]
    out = plan_join(
        main_entity="order_revenue", target_entity="dim_user", joins=joins,
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "ambiguous_join"


def test_same_entity_needs_no_join():
    """目标实体就是主实体 → 不该走到本函数（调用方的判断错了）。"""
    out = plan_join(
        main_entity="order_revenue", target_entity="order_revenue", joins=_JOINS,
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(out, Refusal)
    assert out.rule == "no_join_needed"
    # 每一条拒绝都要给出路（全局约束），brief 这条漏了 allowed
    assert out.allowed


# ---------------------------------------------------------------- 结构约定


def test_plan_and_refusal_are_frozen():
    """决策结果不该被下游改写 —— compiler 只负责把它变成字符串。"""
    plan = plan_join(
        main_entity="order_revenue", target_entity="dim_user", joins=_JOINS,
        agg_expr="SUM(x)", additivity=None,
    )
    assert isinstance(plan, JoinPlan)
    with pytest.raises(FrozenInstanceError):
        plan.to_entity = "x"  # type: ignore[misc]

    out = plan_join(
        main_entity="order_revenue", target_entity="order_revenue", joins=_JOINS,
        agg_expr="SUM(x)", additivity=None,
    )
    with pytest.raises(FrozenInstanceError):
        out.rule = "x"  # type: ignore[misc]


def test_planner_never_touches_db_or_network():
    """零 DB 依赖是全局约束：模块不该 import 任何 IO/ORM 依赖。"""
    from app.api.v1.module_semantic.retrieval import join_planner

    src = join_planner.__doc__ or ""
    assert src  # 有 docstring
    import inspect

    code = inspect.getsource(join_planner)
    for forbidden in ("sqlalchemy", "httpx", "requests", "AsyncSession", "await "):
        assert forbidden not in code, f"planner 不该出现 {forbidden}"
