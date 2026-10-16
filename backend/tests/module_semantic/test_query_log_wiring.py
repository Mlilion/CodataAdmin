"""日志接入点：search_semantic / query_indicator 真的会记日志（§6 闭环的第一环）。

地基（表 + service + 挖掘）已就位，但没有接入点表就永远是空的 —— 挖掘跑在空表上
只会返回 []，看起来"没问题"，实际上闭环从未启动。这类"建好了但没接线"的缺陷正是
本轮反复踩到的（`force_data_layer` 参数存在却无人传，也是同一形态）。

## 三条不可让步的性质

1. **日志失败绝不影响检索/取数** —— 它是旁路观测。让它把一次成功的取数变成失败，
   比不记日志糟糕得多。
2. **独立 DB 会话** —— 与业务事务隔离。共用会话时，日志写入报错会污染业务事务
   （SQLAlchemy 的会话在异常后处于不可用状态），等于让旁路拖垮主链。
3. **零召回也要记** —— 那恰恰是最有价值的信号（别名缺失的直接证据），
   若只在有结果时记，零召回清单永远是空的。
"""

import inspect

import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.querylog.model import SemQueryLogModel
from app.core.database import async_db_session


async def _logs(raw_query: str) -> list[SemQueryLogModel]:
    async with async_db_session() as db:
        return list((await db.execute(
            select(SemQueryLogModel).where(SemQueryLogModel.raw_query == raw_query)
        )).scalars().all())


@pytest.mark.asyncio
async def test_search_semantic_records_log(_api_client, monkeypatch):
    """★★ search_semantic 调用后必须留下一条日志（否则闭环第一环缺失）。"""
    from app.api.v1.module_semantic.retrieval import tools

    async def _fake_search(query, model_id, top_k, node_types):
        return {
            "ok": True,
            "items": [{"type": "indicator", "code": "sub_revenue", "name": "订阅收入",
                       "score": 0.93, "match": "alias",
                       "needs_clarify": False, "not_buildable": False}],
            "time_range": None, "ambiguous": None, "rejected": None,
            "keyword": "订阅收入",
        }

    monkeypatch.setattr(tools, "_do_search", _fake_search)

    await tools.search_semantic("6月订阅收入是多少")

    rows = await _logs("6月订阅收入是多少")
    assert rows, "search_semantic 没有记录 query 日志"
    row = rows[0]
    assert row.matched_code == "sub_revenue"
    assert row.matched_type == "indicator"
    assert row.candidate_count == 1
    assert row.adopted is None, "刚记录时不该有采纳结论"


@pytest.mark.asyncio
async def test_search_records_zero_recall(_api_client, monkeypatch):
    """★★ 零召回必须也记 —— 它是别名缺失最直接的证据。

    只在有结果时记日志，`zero_recall_queries()` 就永远返回空，
    那个清单等于白做。
    """
    from app.api.v1.module_semantic.retrieval import tools

    async def _empty(query, model_id, top_k, node_types):
        return {"ok": True, "items": [], "time_range": None, "ambiguous": None,
                "rejected": {"reason": "无候选", "did_you_mean": []}, "keyword": "毛利"}

    monkeypatch.setattr(tools, "_do_search", _empty)

    await tools.search_semantic("毛利有多少")

    rows = await _logs("毛利有多少")
    assert rows, "零召回的 query 没被记录"
    assert rows[0].candidate_count == 0
    assert rows[0].matched_code is None


@pytest.mark.asyncio
async def test_logging_failure_does_not_break_search(_api_client, monkeypatch):
    """★★ 日志写入炸了，检索结果照样返回 —— 旁路不得拖垮主链。"""
    from app.api.v1.module_semantic.retrieval import tools

    async def _fake_search(query, model_id, top_k, node_types):
        return {"ok": True, "items": [], "time_range": None,
                "ambiguous": None, "rejected": None, "keyword": "k"}

    async def _boom(*a, **kw):
        raise RuntimeError("日志库挂了")

    monkeypatch.setattr(tools, "_do_search", _fake_search)
    monkeypatch.setattr(tools, "_log_search", _boom)

    out = await tools.search_semantic("订阅收入")

    assert out["ok"] is True, f"日志失败把检索也带崩了: {out}"


@pytest.mark.asyncio
async def test_search_result_shape_unchanged(_api_client, monkeypatch):
    """★ 接入日志不能改变工具返回给 agent 的结构（内部字段不该泄漏出去）。

    `keyword` 是给日志用的内部字段，不该出现在 agent 看到的返回里 ——
    工具契约是 LLM 的输入，多一个无用字段就是多一份噪声。
    """
    from app.api.v1.module_semantic.retrieval import tools

    async def _fake_search(query, model_id, top_k, node_types):
        return {"ok": True, "items": [], "time_range": None,
                "ambiguous": None, "rejected": None, "keyword": "内部用"}

    monkeypatch.setattr(tools, "_do_search", _fake_search)

    out = await tools.search_semantic("订阅收入")

    assert "keyword" not in out, "内部字段 keyword 泄漏到 agent 返回里"


@pytest.mark.asyncio
async def test_query_indicator_marks_adoption(_api_client, monkeypatch):
    """★★ 取数成功 = agent 采纳了那个指标 —— 这是最可靠的正例信号。

    参考方案 §6 的「采纳日志」不需要用户点赞：agent 拿某个 code 真去取数并成功，
    就是它认可了检索结果。把这个动作接成正例，闭环无需额外 UI 即可运转。
    """
    from app.api.v1.module_semantic.retrieval import tools

    async def _fake_query(model_id, payload, max_rows, draft_hint=None):
        class _Out:
            def to_dict(self):
                return {"ok": True, "rows": [{"v": 1}], "errors": []}
        return _Out()

    monkeypatch.setattr(tools, "_do_query", _fake_query)

    # 先造一条检索日志（同一 query 命中了 sub_revenue）
    async def _fake_search(query, model_id, top_k, node_types):
        return {"ok": True,
                "items": [{"type": "indicator", "code": "sub_revenue", "name": "订阅收入",
                           "score": 0.9, "match": "alias",
                           "needs_clarify": False, "not_buildable": False}],
                "time_range": None, "ambiguous": None, "rejected": None,
                "keyword": "订阅收入"}

    monkeypatch.setattr(tools, "_do_search", _fake_search)
    await tools.search_semantic("订阅收入多少")

    await tools.query_indicator(
        model_id=1, indicator="sub_revenue", start="2026-06-01", end="2026-06-30",
    )

    rows = await _logs("订阅收入多少")
    assert rows, "前提失败：检索日志没记上"
    assert rows[0].adopted is True, (
        f"取数成功未回填采纳信号(adopted={rows[0].adopted}) —— "
        f"挖掘只认 adopted=True, 不回填则永远挖不出别名"
    )


@pytest.mark.asyncio
async def test_failed_query_does_not_mark_adoption(_api_client, monkeypatch):
    """★ 取数失败不算采纳 —— 否则把校验拒绝的错误指标也当成正例。"""
    from app.api.v1.module_semantic.retrieval import tools

    async def _fake_search(query, model_id, top_k, node_types):
        return {"ok": True,
                "items": [{"type": "indicator", "code": "arpu", "name": "ARPU",
                           "score": 0.9, "match": "alias",
                           "needs_clarify": False, "not_buildable": False}],
                "time_range": None, "ambiguous": None, "rejected": None,
                "keyword": "人均收入"}

    async def _fail_query(model_id, payload, max_rows, draft_hint=None):
        class _Out:
            def to_dict(self):
                return {"ok": False, "rows": [],
                        "errors": [{"rule": "not_buildable", "message": "本模型不可算"}]}
        return _Out()

    monkeypatch.setattr(tools, "_do_search", _fake_search)
    monkeypatch.setattr(tools, "_do_query", _fail_query)

    await tools.search_semantic("人均收入是多少")
    await tools.query_indicator(model_id=1, indicator="arpu",
                                start="2026-06-01", end="2026-06-30")

    rows = await _logs("人均收入是多少")
    assert rows[0].adopted is not True, "取数失败却被记成采纳正例"


# ---------------------------------------------------------------- 调用方标识


@pytest.mark.asyncio
async def test_search_log_records_caller(_api_client, monkeypatch):
    """★★ 日志要记下调用方 —— 否则治理时答不出"是谁在这么问"。

    此前 `asked_by_id` 永远是空（grep 全仓：只有定义与形参，无任何调用者传值），
    于是同义词回流闭环能挖出"哪些说法该补别名"，却分不清是自动巡检还是真人在问 ——
    而两者的采纳标准不同（脚本里写死的措辞采纳成别名没有价值）。
    """
    from app.api.v1.module_semantic.retrieval import tools

    async def _fake_search(query, model_id, top_k, node_types):
        return {"ok": True, "keyword": "订阅收入", "items": [], "time_range": None,
                "ambiguous": None, "rejected": None, "unavailable": None}

    monkeypatch.setattr(tools, "_do_search", _fake_search)
    monkeypatch.setattr(tools, "_current_caller", lambda: "codata")

    await tools.search_semantic("带调用方的问法")

    rows = await _logs("带调用方的问法")
    assert rows, "日志没记上"
    assert rows[0].caller == "codata", f"调用方未记录: {rows[0].caller}"


@pytest.mark.asyncio
async def test_caller_identification_failure_does_not_break_search(_api_client, monkeypatch):
    """★★ 识别调用方失败时检索照常返回 —— 旁路不得拖垮主链。

    `get_http_headers()` 在非 HTTP 上下文（单测直调 tool、Agno 本地调用）取不到甚至
    抛异常。若不兜底，一个纯观测用的字段就能让取数整体失败。
    """
    from app.api.v1.module_semantic.retrieval import tools

    async def _fake_search(query, model_id, top_k, node_types):
        return {"ok": True, "keyword": "k", "items": [], "time_range": None,
                "ambiguous": None, "rejected": None, "unavailable": None}

    def _boom():
        raise RuntimeError("不在 HTTP 上下文")

    monkeypatch.setattr(tools, "_do_search", _fake_search)
    monkeypatch.setattr(tools, "_current_caller", _boom)

    out = await tools.search_semantic("识别失败也要能搜")

    assert out["ok"] is True, f"识别调用方失败把检索也带崩了: {out}"


def test_current_caller_never_raises_outside_http(monkeypatch):
    """★ 非 HTTP 上下文下 `_current_caller()` 不得抛异常（返回值可为 None 或 anonymous）。

    ★ 我最初断言它必须返回 None，实测是 `anonymous` —— 那才是对的：
    dev 未配任何 token 时 `identify_caller(None)` 按设计返回 `anonymous`
    （"确实没有鉴权"与"识别失败"是两种状态，不该混成同一个 None）。
    这条只保证**不抛**，具体值由 identify_caller 的语义决定，别在这里重复断言。
    """
    from app.api.v1.module_semantic.retrieval.tools import _current_caller

    assert _current_caller() in (None, "anonymous")

    # 配了 token 但不在 HTTP 上下文 → 取不到头 → 识别不出，仍不抛
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_TOKENS", "codata:tok-a")
    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", "")

    assert _current_caller() is None


@pytest.mark.asyncio
async def test_current_caller_asks_fastmcp_to_include_authorization():
    """★★★ 必须显式要 authorization 头，否则 caller 永远是 None。

    ## dev 库实跑才暴露出来的缺陷

    `fastmcp.get_http_headers()` **默认剥掉 `authorization`**（它的 exclude 名单里
    明确列着，理由是"转发给下游服务会出问题"）。而 `_current_caller` /
    `build_mcp_auth` 都靠重新解析这个头来识别身份 —— 拿到的 dict 里根本没有它，
    于是：

    - `caller` 字段**从加上那天起就一直是 None**（治理页筛选框永远空）
    - `build_mcp_auth` 拿不到绑定用户 → `user=None` → **受限模型对谁都不可见**
      （用户隔离在真实链路上等于没生效）

    单测无法暴露：所有用例都直调 tool 函数（非 HTTP 上下文），走的是
    `except → None` 兜底分支，与"在 HTTP 上下文里拿不到头"表现一致。

    ★ 本用例断言的是**调用方式**而非结果：要求传 `include={"authorization"}`。
    这是能在无 HTTP 上下文下固化住该约定的唯一办法 —— 而这个约定一旦丢，
    两条身份链路会同时静默失效（静默是这里最糟的部分）。
    """
    import inspect

    from app.api.v1.module_semantic.retrieval import tools

    src = inspect.getsource(tools)

    # 两处调用点都必须显式要 authorization
    calls = [ln for ln in src.splitlines() if "get_http_headers(" in ln and "import" not in ln]
    assert len(calls) >= 2, f"调用点数量变了，请同步本用例: {calls}"
    for ln in calls:
        assert "authorization" in ln.lower(), (
            f"get_http_headers 未显式包含 authorization —— fastmcp 默认剥掉它，"
            f"caller 与用户隔离都会静默失效。问题行: {ln.strip()}"
        )


def test_fastmcp_still_strips_authorization_by_default():
    """★ 固化住"为什么必须传 include"这个前提。

    若 fastmcp 某天改成默认保留 authorization，本用例会红 —— 那时可以简化上面的
    要求。反之若它继续剥，这条就是上面那条断言的依据，别把两条都删掉。
    """
    from fastmcp.server.dependencies import get_http_headers

    src = inspect.getsource(get_http_headers)

    assert '"authorization"' in src, (
        "fastmcp 的 exclude 名单里已无 authorization，"
        "请复核 _current_caller / build_mcp_auth 是否还需要 include 参数"
    )
