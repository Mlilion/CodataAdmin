"""跨实体维度必须先进指标白名单，功能才可达 —— 并钉住"白名单只比 code、不比实体"。

## 这个测试防的是什么

`validator` 规则 2（`validator.py` 的「维度白名单」段）用
`indicator["available_dimensions"]` 作**硬白名单**，而该字段来自
`indicator.available_dimension_ids` —— **纯人工维护**，`indicator/service.py`
里没有任何自动填充，别处也没有。

dev 实测（`commercial_revenue` 最新 Release 快照，20 指标 / 29 维度）：
**20/20 个指标的白名单里，跨实体维度数 = 0。**

于是受控两表 JOIN 编译（Tasks 1–5）全部上线之后，"按用户城市看订阅收入"这类问法
**仍会在校验阶段被 `dimension_allowed` 拦死**，压根走不到 join 分流。
「有能力但没人打开它」与「没有这个能力」对使用者是同一种体验 —— 这与本分支反复修过的
那批"系统摆出一个入口、照着做却做不成"同源，所以必须有一处可执行的东西钉住它。

## 为什么这个测试"不用改代码就过"，却仍然必须存在

校验器比的是**维度 code**，它压根不看该维度绑在哪个实体上（规则 2 只做
`dim not in dimensions` 与 `dim not in allowed_dims` 两次集合判断）。所以一个
**已在白名单里**的跨实体维度**本来就**能过校验 —— 本文件两条用例在写下时即通过，
这是**预期结果**，不是"测试没测到东西"。

它的职责是把这个不变量**钉死**：日后有人看到"维度不在主实体上"觉得可疑，
"顺手加个实体校验"（比如要求 `bindings[].entity == indicator.primary_entity`），
跨实体下钻能力会**整条静默失效** —— 拒绝文案还是 `dimension_allowed`，
看起来像是治理侧没勾维度，没有任何人会怀疑到校验器上。届时本文件会红。

跨实体维度**能否到达**是可达性问题，由 `join_planner`（有无边 / 是否多跳 / 多边歧义 /
fan-out）与 `pipeline`（口径能否分解）判，各自给出自己的 rule；
**不是**白名单问题。两件事必须分开由两层管。

启用步骤见 `backend/sql/patches/2026-08-04-semantic-cross-entity-dimension-note.sql`
与部署清单第 7b 步。
"""

from app.api.v1.module_semantic.retrieval.validator import QueryIntent, validate_intent


def _ctx() -> dict:
    """一个最小语义上下文：`dim_user_city` 绑在 `dim_user` 上、指标主实体是 `order_revenue`。

    即维度**跨实体**，但**已在白名单里**（`available_dimensions` 含它）——
    正是治理侧照 SQL 补丁勾选并重新发布之后的形态。

    ★ `lifecycle_status: 2`（已发布）不可省。上一版漏了它，于是主用例是在
    `verdict.ok is False` / `errors=['lifecycle_published']` 的状态下通过的 ——
    两条"不含 dimension_* 规则"的缺席断言仍然成立，但校验器**在生命周期那一关就返回了**。
    真出现"顺手加个实体校验"且它排在生命周期检查之后（短路）时，本文件不会红，
    正好漏掉它唯一要catch的那个回归。补上之后可以直接断言 `verdict.ok is True`。
    """
    return {
        "indicators": [{
            "code": "sub_revenue",
            "primary_entity": "order_revenue",
            "lifecycle_status": 2,  # 已发布 —— 否则校验器在这一关就短路返回
            "available_dimensions": ["dim_user_city"],  # 跨实体维度已在白名单
            "impls": [{
                "role": "primary", "review_status": 1,
                "sql_text": (
                    "SELECT SUM(final_revenue) FROM dws.t "
                    "WHERE order_date BETWEEN '{start}' AND '{end}'"
                ),
            }],
        }],
        "dimensions": [{
            "code": "dim_user_city",
            "bindings": [{"entity": "dim_user", "field": "city"}],
        }],
        "entities": [
            {"code": "order_revenue", "fields": []},
            {"code": "dim_user", "fields": [{"code": "city", "column": "city"}]},
        ],
        "knowledge": [],
    }


def test_whitelisted_cross_entity_dimension_passes_validation():
    """★ 已在白名单里的跨实体维度不该被校验器拒 —— 能否到达由 join_planner 判。

    这条**在写下时即通过**（校验器只比 code）。它防的是日后被加进一条
    "维度必须绑在指标主实体上"的检查：那会让跨实体下钻整条静默失效，
    且拒绝文案仍是 `dimension_allowed`，看着像治理侧没勾维度。

    ★ 终审 Min 4：必须断言 `verdict.ok is True`，不能只断言"没有 dimension_* 规则"。
    上一版 fixture 漏了 `lifecycle_status`，实测这条用例是在
    `ok=False, errors=['lifecycle_published']` 的状态下绿的 —— 校验器压根没走到维度那关。
    那样一来，日后真被加进一条**排在生命周期检查之后**的实体校验时，
    这条用例仍然绿，正好放过它唯一要catch的那个回归。
    """
    verdict = validate_intent(
        QueryIntent(
            indicator="sub_revenue",
            dimensions=["dim_user_city"],
            time={"start": "2026-07-01", "end": "2026-07-07"},
        ),
        _ctx(),
    )

    assert verdict.ok, (
        f"已发布指标 + 已勾选的跨实体维度必须整条过校验；errors={verdict.errors}"
    )
    rules = {e.rule for e in verdict.errors}
    assert "dimension_exists" not in rules, (
        f"维度确实存在于模型的 dimensions 里，不该报不存在；errors={verdict.errors}"
    )
    assert "dimension_allowed" not in rules, (
        f"它已在该指标的 available_dimensions 里，不该报不允许；errors={verdict.errors}"
    )


def test_non_whitelisted_dimension_still_refused():
    """白名单仍然有效 —— 不在名单里的照旧拒。

    与上一条成对：证明上一条通过不是因为"白名单形同虚设"，而确实是因为那个维度
    被勾上了。否则第一条用例可能在校验器彻底失效的情况下也照样绿。
    """
    ctx = _ctx()
    ctx["dimensions"].append({
        "code": "dim_bill_type",
        "bindings": [{"entity": "iap_bill_detail", "field": "bill_type"}],
    })

    verdict = validate_intent(
        QueryIntent(
            indicator="sub_revenue",
            dimensions=["dim_bill_type"],
            time={"start": "2026-07-01", "end": "2026-07-07"},
        ),
        ctx,
    )

    assert "dimension_allowed" in {e.rule for e in verdict.errors}, (
        f"未进白名单的维度必须被拒；errors={verdict.errors}"
    )


def test_refusal_lists_the_whitelist_so_governance_gap_is_visible():
    """拒绝时必须把白名单原样列在 `allowed` 里 —— 那是"治理侧没勾"的唯一线索。

    没有它，agent 只知道"不支持按 X 拆分"，无从判断这是**能力缺口**还是
    **白名单没勾**；而这两者的处置完全不同（前者要改口径/换指标，后者只需治理侧
    勾一下维度再发布）。dev 实测白名单里跨实体维度为 0，所以这条线索在真实部署上
    恰恰是最常需要的那条。
    """
    ctx = _ctx()
    ctx["dimensions"].append({
        "code": "dim_bill_type",
        "bindings": [{"entity": "iap_bill_detail", "field": "bill_type"}],
    })

    verdict = validate_intent(
        QueryIntent(
            indicator="sub_revenue",
            dimensions=["dim_bill_type"],
            time={"start": "2026-07-01", "end": "2026-07-07"},
        ),
        ctx,
    )

    refusal = next(e for e in verdict.errors if e.rule == "dimension_allowed")
    assert refusal.allowed == ["dim_user_city"], (
        f"allowed 必须是该指标当前的白名单原文；实际 {refusal.allowed}"
    )
