"""多口径选择：primary 必须按 role 选，不能靠 impls[0] 的位置运气。

## 缺口（参考方案 §8 P2「多口径 EXPLAIN 择优」的前置问题）

`validator.py:215` 与 `pipeline.py` 都写着 `primary = impls[0]`，把「主口径」等同于
「数组第一项」。但快照编译（`service.py`）加载 impl 时**没有 ORDER BY**：

    select(SemIndicatorImplModel).where(indicator_id.in_(...), is_deleted.is_(False))

顺序由数据库返回顺序决定。所以一旦某指标有多个审核通过的口径，且 secondary 恰好排在
前面，系统就会拿**副口径当权威口径**去校验时间窗、判粒度护栏、填参执行 —— 数字照样
返回，口径已经错了，而这正是方案 §4 说的「最危险」那一类（Q10：数据查得出、SQL 也没错，
但口径错了）。

参考方案 §5 明确 `search_indicators` 的多口径形态：*「含主口径(PRIMARY)+副口径
(SECONDARY，常是 ADS 预聚合层的高效物化)」*——副口径存在就是常态，不是边缘情况。

★ 现状是**潜伏**缺陷：dev 快照 v15 的 20 个指标全是单口径，所以还没炸过。
但 bill 模型 YAML 里 `renewal_rate` 等已规划多口径，一旦补上就会踩。
"""


from app.api.v1.module_semantic.retrieval.validator import QueryIntent, validate_intent


def _context(impls: list[dict]) -> dict:
    """最小快照上下文：一个指标 + 给定的口径列表。"""
    return {
        "model": {"code": "m", "version": 1},
        "entities": [{
            "code": "e",
            "physical": {"database": "dws", "table": "t"},
            "fields": [{"code": "amt", "column": "amt"}],
        }],
        "dimensions": [],
        "indicators": [{
            "code": "rev",
            "name": "收入",
            "primary_entity": "e",
            "lifecycle_status": 2,
            "available_dimensions": [],
            "impls": impls,
        }],
        "knowledge": [],
    }


_PRIMARY_DWS = {
    "role": "primary", "sql_text": "SELECT SUM(amt) FROM dws.t WHERE dt BETWEEN '{start}' AND '{end}'",
    "data_layer": "DWS", "granularity": "day", "review_status": 1,
}
# 副口径落 DWD 明细层 + order 粒度 —— 正是粒度护栏(规则7)要拦的组合
_SECONDARY_DWD = {
    "role": "secondary", "sql_text": "SELECT SUM(amt) FROM dwd.t WHERE dt BETWEEN '{start}' AND '{end}'",
    "data_layer": "DWD", "granularity": "order", "review_status": 1,
}


def test_grain_guard_fires_on_primary_regardless_of_position():
    """★★ 主口径合法时不该被副口径的粒度问题拖累（primary 在后也要认得出）。

    构造：secondary(DWD/order，会触发粒度护栏) 排在 primary(DWS/day，合法) 前面。
    正确行为 = 按 role 找到 primary → 合法 → 放行。
    现状 impls[0] = secondary → 误判粒度护栏 → **合法查询被拒**。
    """
    ctx = _context([_SECONDARY_DWD, _PRIMARY_DWS])  # 注意顺序：secondary 在前
    intent = QueryIntent(indicator="rev", dimensions=[], filters=[],
                         time={"start": "2026-06-01", "end": "2026-06-30"})

    verdict = validate_intent(intent, ctx)

    rules = [e.rule for e in verdict.errors]
    assert "grain_guard" not in rules, (
        f"按位置取 impls[0] 拿到了 secondary(DWD/order)，"
        f"把合法的 primary(DWS/day) 查询误判为粒度冲突: {rules}"
    )
    assert verdict.ok, f"主口径合法却被拒: {[e.message for e in verdict.errors]}"


def test_snapshot_time_requirement_reads_primary_not_first():
    """★ 时点类判定（{as_of}）也依赖 primary —— 取错口径会要错时间参数。

    构造：primary 是时点口径（用 {as_of}），secondary 是区间口径（用 {start}/{end}）
    且排在前面。用户按时点传 as_of 是对的。
    现状 impls[0]=secondary → 认为需要 start/end → 把正确请求判成缺时间窗。
    """
    primary_asof = {
        "role": "primary", "sql_text": "SELECT COUNT(*) FROM dws.t WHERE d <= '{as_of}'",
        "data_layer": "DWS", "granularity": "snapshot", "review_status": 1,
    }
    secondary_range = {
        "role": "secondary", "sql_text": "SELECT COUNT(*) FROM dws.t WHERE dt BETWEEN '{start}' AND '{end}'",
        "data_layer": "DWS", "granularity": "day", "review_status": 1,
    }
    ctx = _context([secondary_range, primary_asof])  # secondary 在前
    intent = QueryIntent(indicator="rev", dimensions=[], filters=[],
                         time={"as_of": "2026-06-30"})

    verdict = validate_intent(intent, ctx)

    assert verdict.ok, (
        f"primary 是 {{as_of}} 时点口径、用户也传了 as_of，却被判失败 "
        f"(说明读的是 impls[0]=secondary 的区间口径): "
        f"{[(e.rule, e.message) for e in verdict.errors]}"
    )


def test_grain_guard_still_fires_when_primary_is_bad():
    """★ 反向：primary 自己就是 DWD/order 时，粒度护栏必须照常拦（别把牙拔掉）。"""
    ctx = _context([{**_SECONDARY_DWD, "role": "primary"}])
    intent = QueryIntent(indicator="rev", dimensions=[], filters=[],
                         time={"start": "2026-06-01", "end": "2026-06-30"})

    verdict = validate_intent(intent, ctx)

    assert "grain_guard" in [e.rule for e in verdict.errors], "粒度护栏该拦却没拦"
