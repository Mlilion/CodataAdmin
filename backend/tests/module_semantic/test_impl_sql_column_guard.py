"""权威口径 SQL 与物理列的漂移守卫（参考方案 §9 坑 6「索引与语义层口径漂移」）。

## 为什么需要这一层

`impl.sql_text` 是整个方案里**唯一"直接拿去执行、却没有任何校验"**的东西。
方案 §3⑦ 的立论是「SQL 不生成、查表得到」——幻觉免疫的代价是：口径 SQL 的正确性
完全依赖人工，而人工写的 SQL 也会因**上游改表**而失效。

现有校验没有一处能拦住它：

| 环节 | 查的是什么 | 能拦幽灵列吗 |
|---|---|---|
| `schema.py` / `model.py` 的 `sql_text` validator | 非空 | ❌ |
| `completeness.field_completeness` | `physical_column_id` 是否 orphan | ❌ 只看 field，不看 SQL |
| `validator.py` 12 条规则 | 参数（维度/枚举/时间窗） | ❌ 一条都不读 SQL 文本 |
| `executor.guard_readonly` | DDL/DML 关键字 | ❌ 只防写操作 |
| `executor` 的 EXPLAIN 预检 | 数据库判可执行性 | ✅ **但已是取数那一刻** |

所以 2026-07-31 上游把 `dws_order_revenue_df` 重构为 `dws_revenue_order_df` 并删掉
`is_paid_order` 等 5 列时，14 个写着 `WHERE is_paid_order = 1` 的 impl 一路通过
"发布 v12 全绿"，直到用户真去问数才炸在 EXPLAIN 上。

## 这个守卫的定位

EXPLAIN 确实会拦住（实测报 `Column 'is_paid_order' cannot be resolved`），所以后果
不是"静默返回错数"——是**发布时说全绿、取数时才炸**，且错误面向终端用户。
守卫的价值是把发现点从「用户提问」左移到「发布/CI」。

★ 判据锚在快照自身：`entities[].fields[]` 声明的名字集合 vs `impls[].sql_text`
引用的标识符。两者同源于一次发布，不依赖外部 YAML 或数仓连接，因此在 CI 里可跑。

守卫实现在 `app/.../model/snapshot_audit.py`（产品代码，发布接口会调），本文件只测它。
"""

import json

import pytest

from app.api.v1.module_semantic.model.snapshot_audit import audit_snapshot

# ------------------------------------------------------------------ 守卫本体


def test_audit_catches_ghost_column():
    """★ 守卫本身有牙：构造一个引用已删列的快照，必须被抓出来。

    这是这次真实事故的最小复现 —— `is_paid_order` 不在 fields 里，
    却出现在权威口径 SQL 的 WHERE 中。
    """
    snapshot = {
        "entities": [{
            "code": "order_revenue",
            "physical": {"database": "dws", "table": "dws_revenue_order_df"},
            "fields": [
                {"code": "final_revenue", "column": "final_revenue"},
                {"code": "order_date", "column": "order_date"},
                {"code": "order_type", "column": "order_type"},
            ],
        }],
        "indicators": [{
            "code": "sub_revenue",
            "impls": [{
                "role": "primary",
                "sql_text": (
                    "SELECT SUM(final_revenue) AS sub_revenue\n"
                    "FROM dws.dws_revenue_order_df\n"
                    "WHERE order_date BETWEEN '{start}' AND '{end}'\n"
                    "  AND is_paid_order = 1 AND order_type = 'subscription'"
                ),
            }],
        }],
    }

    problems = audit_snapshot(snapshot)

    assert problems, "引用已删列的口径 SQL 未被抓出 —— 守卫失效"
    assert "is_paid_order" in problems[0]


def test_audit_accepts_clean_snapshot():
    """★ 不误报：合法 SQL（含枚举字面量/中文注释/别名/占位符）必须全绿。

    第一版守卫在这里翻车过：把 `'subscription'`（枚举值）、`AS sub_revenue`（别名）、
    `{start}`（占位符）全当成列名，21 条 impl 报出 20 条"异常"。
    误报率高的守卫等于没有守卫 —— 没人会去看它。
    """
    snapshot = {
        "entities": [{
            "code": "order_revenue",
            "physical": {"database": "dws", "table": "dws_revenue_order_df"},
            "fields": [
                {"code": "final_revenue", "column": "final_revenue"},
                {"code": "order_date", "column": "order_date"},
                {"code": "order_type", "column": "order_type"},
                {"code": "renew_order_subtype", "column": "renew_order_subtype"},
            ],
        }],
        "indicators": [{
            "code": "upgrade_cnt",
            "impls": [{
                "role": "primary",
                "sql_text": (
                    "-- 升级单：取 renew_order_subtype，不在 order_class 找\n"
                    "SELECT COUNT(*) AS upgrade_cnt\n"
                    "FROM dws.dws_revenue_order_df\n"
                    "WHERE order_date BETWEEN '{start}' AND '{end}'\n"
                    "  AND order_type = 'subscription'\n"
                    "  AND renew_order_subtype = 'upgrade'"
                ),
            }],
        }],
    }

    assert audit_snapshot(snapshot) == [], "合法口径 SQL 被误报"


def test_audit_handles_expr_field():
    """expr 字段（无单列绑定）引用的列也算已声明 —— 否则计算字段全被误报。"""
    snapshot = {
        "entities": [{
            "code": "e",
            "physical": {"database": "dws", "table": "t"},
            "fields": [
                {"code": "gross", "column": "gross"},
                {"code": "margin", "expr": "gross - platform_fee"},
            ],
        }],
        "indicators": [{
            "code": "m",
            "impls": [{"role": "primary",
                       "sql_text": "SELECT SUM(platform_fee) FROM dws.t"}],
        }],
    }

    assert audit_snapshot(snapshot) == [], "expr 里声明的列被误判为幽灵列"


def test_audit_catches_ghost_behind_table_alias():
    """★ 别名限定的幽灵列也要抓：`a.is_paid_order` 不能因为带了 `a.` 就蒙过去。

    踩过：早期实现用 `\\b\\w+\\.\\w+\\b → 空格` 整段剥掉限定名（为了滤掉
    `dws.dws_revenue_order_df`），结果把 `a.is_paid_order` 一起吞了 —— 凡是写了表别名
    的 SQL，幽灵列全部漏报。现在只剥前缀、保留列名。
    """
    snapshot = {
        "entities": [{
            "code": "e",
            "physical": {"database": "dws", "table": "dws_revenue_order_df"},
            "fields": [{"code": "final_revenue", "column": "final_revenue"}],
        }],
        "indicators": [{
            "code": "sub_revenue",
            "impls": [{
                "role": "primary",
                "sql_text": (
                    "SELECT SUM(a.final_revenue) AS rev\n"
                    "FROM dws.dws_revenue_order_df a\n"
                    "WHERE a.is_paid_order = 1"
                ),
            }],
        }],
    }

    problems = audit_snapshot(snapshot)

    assert problems, "带表别名的幽灵列漏报了"
    assert "is_paid_order" in problems[0]


def test_audit_ignores_empty_and_missing_sql():
    """空/缺失 sql_text 不该炸也不该误报 —— 它由 schema 层的非空校验负责。"""
    assert audit_snapshot({}) == []
    assert audit_snapshot({"indicators": [{"code": "x", "impls": [{"role": "primary"}]}]}) == []


@pytest.mark.asyncio
async def test_publish_rejects_ghost_column_sql(seed_model_knowledge_all_targets):
    """★★ 发布接口必须**拦住**引用幽灵列的口径 —— 不能只在 CI 里报。

    发布是口径进入 agent 可见范围的唯一入口。只在 CI 拦意味着：本地/生产手工点发布
    照样能把引用幽灵列的口径推上去，等用户问数才炸。

    这里改活表里的 impl SQL（模拟"上游删列后没同步口径"），再调真实 publish()。
    """
    from sqlalchemy import select

    from app.api.v1.module_semantic.indicator.model import SemIndicatorImplModel
    from app.core.exceptions import CustomException

    svc, model_id, _ = seed_model_knowledge_all_targets
    db = svc.auth.db

    impl = (await db.execute(select(SemIndicatorImplModel))).scalars().first()
    impl.sql_text = (
        "SELECT SUM(final_revenue) FROM db_kn.t_kn_order WHERE is_paid_order = 1"
    )
    await db.flush()

    with pytest.raises(CustomException) as exc:
        await svc.publish(model_id)

    assert "is_paid_order" in str(exc.value.msg), f"错误消息没点出幽灵列: {exc.value.msg}"


@pytest.mark.asyncio
async def test_publish_succeeds_when_sql_is_clean(seed_model_knowledge_all_targets):
    """★ 守卫不能把正常发布焊死 —— 干净的模型必须仍能发布。

    与上一条配对：只证"能拦"不够，还要证"不乱拦"。守卫误报的代价是发布链路瘫掉，
    比漏报更严重（漏报只是回到 EXPLAIN 兜底的现状）。
    """
    svc, model_id, _ = seed_model_knowledge_all_targets

    out = await svc.publish(model_id)

    assert out.version >= 1


@pytest.mark.asyncio
async def test_published_snapshot_has_no_ghost_columns(seed_model_knowledge_all_targets):
    """★★ 真发布一次，快照里的权威口径不得引用未声明的列。

    这条接在真实发布路径上：任何人改 impl SQL 或删字段后重新发布，
    这里就会红 —— 把发现点从"用户提问时 EXPLAIN 报错"左移到 CI。
    """
    svc, model_id, _ = seed_model_knowledge_all_targets
    out = await svc.publish(model_id)
    release = await svc.release_detail(out.id)
    snapshot = (
        release.snapshot if isinstance(release.snapshot, dict)
        else json.loads(release.snapshot)
    )

    problems = audit_snapshot(snapshot)

    assert not problems, "发布的快照里有口径 SQL 引用未声明的列:\n  " + "\n  ".join(problems)
