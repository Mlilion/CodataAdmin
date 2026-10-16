"""语义取数工具集（P4-4）：给 agent 用的 tool 函数。

三个工具对应参考方案在线链路的三段：
- `search_semantic`：找到哪个（P3 检索，返回 id+score）
- `get_model_context`：它是什么（P3 hydrate，读 Release 快照的权威口径）
- `query_indicator`：取数（P4 校验 → 填参 → 只读执行）

★ 工具契约：**永不抛异常**。agent 拿到异常栈没法反思，拿到
`{"ok": false, "errors": [...]}` 才能改意图重试。

docstring 会被 Agno 当作工具说明喂给 LLM —— 写清"什么时候用、参数什么含义"，
比在代码注释里写给人看更重要。
"""

import asyncio
from typing import Any

from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.logger import logger

_DEFAULT_MAX_ROWS = 500

# 审计写入的时间上限（秒）。★ 这是**主链路**的延迟保护，不是审计自己的偏好。
#
# `log_raw_sql` 自己开 app 库会话，而它被 await 在取数返回的路上：
# app 库连不上时光开会话就 ~10s（asyncmy 默认连接超时），连接池打满还能等到
# POOL_TIMEOUT=30s（`setting.py`），"仅仅是慢"的库则压根没有上限。没有这个盖子，
# 一个病了的 app 库会把每次 execute_sql 变成十几秒的调用、还照样返回 ok=True ——
# 调用方只看见工具突然极慢，看不出原因。
#
# 取 3s 的理由（都是相对关系，不是拍脑袋的绝对值）：
# - 相对查询自己的预算（SEM_QUERY_TIMEOUT 默认 60s）只是 5%：工具的延迟天花板从
#   60s 变成 63s，量级没变。
# - 比 asyncmy 那 10s 连接超时和 30s 池等待都**先**收手 —— 正是要盖住的两个坑。
# - 又远大于一次健康写入：开会话 + 单行 INSERT + commit 在同机/同网段是个位数毫秒，
#   留了两个数量级余量，所以"只是有点忙"的库不会因此丢行。
_AUDIT_TIMEOUT_SECONDS = 3.0


class NotConfiguredError(RuntimeError):
    """服务端未配置(与"用户参数错"必须区分开)。

    ★ 归因错了的代价很实际: agent 的工具契约说 errors[] 意味着"按 allowed 改正参数
    后重试", 所以把配置缺失报成普通错误会让它**无限重试一个它修不了的问题**。
    """


# ---- 内部实现（测试可 monkeypatch 这三个，验证工具层的异常兜底）----


async def _do_search(query: str, model_id: int | None, top_k: int, node_types: list[str] | None) -> dict:
    from .search import SemanticSearch

    result = await SemanticSearch().search(
        query, node_types=node_types, model_id=model_id, top_k=top_k
    )
    return {
        "ok": True,
        # keyword 是给日志用的内部字段（归一化后的检索词），search_semantic 会剥掉
        # 再返回给 agent —— 工具契约是 LLM 的输入，多一个无用字段就是多一份噪声
        "keyword": result.keyword,
        "items": [
            {
                "type": i.node_type, "code": i.node_code, "name": i.name,
                "score": i.score, "match": i.match_level,
                "needs_clarify": i.needs_clarify, "not_buildable": i.not_buildable,
            }
            for i in result.items
        ],
        "time_range": list(result.time_range) if result.time_range else None,
        "ambiguous": (
            {"message": result.ambiguous.message,
             "candidates": [c.node_code for c in result.ambiguous.candidates]}
            if result.ambiguous else None
        ),
        "rejected": (
            {"reason": result.rejected.reason, "did_you_mean": result.rejected.did_you_mean}
            if result.rejected else None
        ),
        # 与 rejected 平级但语义相反: 这是故障, 不是覆盖边界
        "unavailable": (
            {"reason": result.unavailable.reason, "retryable": result.unavailable.retryable}
            if result.unavailable else None
        ),
    }


def _current_caller() -> str | None:
    """识别本次调用的来源（MCP token 的调用方名）。

    ★ 鉴权在 ASGI 中间件、日志写在 tool 内，两层之间原本没有上下文传递机制。
    fastmcp 提供 `get_http_headers()`，故这里**重新解析**一次 Authorization 头来识别 ——
    但**只做识别、不做放行**：放行只由中间件的 `verify_mcp_token` 判，
    两处都能决定"放不放"必然漂移。

    ★★ 必须传 `include={"authorization"}`：`get_http_headers()` **默认剥掉**该头
    （它的 exclude 名单里明确列着，理由是"转发给下游服务会出问题"）。漏了这个参数
    拿到的 dict 里根本没有 Authorization，`caller` 会**永远是 None** ——
    dev 库实跑才发现的缺陷，单测全部直调 tool（非 HTTP 上下文），走的是下面的
    `except → None` 分支，与"在 HTTP 上下文里拿不到头"表现完全一致，测不出来。

    ★ 非 HTTP 上下文（单测直调 tool、Agno 本地调用）取不到头甚至抛异常 ——
    一律兜底为 None。识别失败绝不能拖垮取数，与 `record()` "永不抛"同一原则。
    """
    try:
        from fastmcp.server.dependencies import get_http_headers

        from ..mcp.auth import extract_bearer, identify_caller

        headers = get_http_headers(include={"authorization"}) or {}
        return identify_caller(extract_bearer(headers))
    except Exception:  # noqa: BLE001 - 识别是旁路，失败即匿名
        return None


async def build_mcp_auth(db):
    """按当前 MCP token 绑定的用户构造 AuthSchema（用户隔离的入口）。

    ★ 只解析身份、不做放行判断 —— 放行由中间件的 `verify_mcp_token` 负责。
    未绑用户 / 非 HTTP 上下文 → `user=None`，此时只能看 public 模型
    （既不当超级权限，也不一概拒绝）。

    ★★ 同 `_current_caller`：必须传 `include={"authorization"}`，否则 fastmcp 默认
    剥掉该头 → 永远解析不出绑定用户 → **受限模型对谁都不可见**，用户隔离在真实
    链路上等于没生效。这个缺陷在 dev 库实跑才暴露（单测直调 tool 不经 HTTP，
    走的是下面的 except 分支，表现与"拿不到头"一致）。

    ★ 解析用户失败一律降级为 user=None 而非抛异常：一个配错的用户名不该让整条
    取数链路不可用，而"只能看 public"是安全方向的降级。
    """
    from app.core.base_schema import AuthSchema

    user = None
    try:
        from fastmcp.server.dependencies import get_http_headers
        from sqlalchemy import select

        from app.api.v1.module_system.user.model import UserModel

        from ..mcp.auth import extract_bearer, resolve_identity

        headers = get_http_headers(include={"authorization"}) or {}
        _caller, username = resolve_identity(extract_bearer(headers))
        if username:
            user = (await db.execute(
                select(UserModel).where(UserModel.username == username)
            )).scalars().first()
            if user is None:
                logger.warning(
                    f"MCP token 绑定的用户[{username}]在库中不存在 —— "
                    f"降级为仅可见 public 模型，请核对 SEM_MCP_TOKENS 配置"
                )
    except Exception as e:  # noqa: BLE001 - 身份解析失败降级, 不拖垮取数
        logger.warning(f"MCP 身份解析失败，降级为匿名: {e}")

    return AuthSchema(db=db, user=user, check_data_scope=False)


async def _log_search(raw_query: str, model_id: int | None, payload: dict) -> None:
    """把一次检索记进 query 日志（§6 闭环第一环）。

    ★ 独立 DB 会话：与业务事务隔离。共用会话时日志写入报错会污染业务事务
    （SQLAlchemy 会话在异常后不可用），等于让旁路观测拖垮主链。

    ★ 零召回也记：那恰恰是最有价值的信号（别名缺失的直接证据）。只在有结果时记，
    `zero_recall_queries()` 就永远是空的。
    """
    from ..querylog.service import QueryLogService

    items = payload.get("items") or []
    top1 = items[0] if items else {}
    async with async_db_session() as db:
        await QueryLogService(AuthSchema(db=db, check_data_scope=False)).record(
            raw_query=raw_query,
            normalized_query=payload.get("keyword") or raw_query,
            model_id=model_id,
            matched_type=top1.get("type"),
            matched_code=top1.get("code"),
            matched_score=top1.get("score"),
            match_path=top1.get("match"),
            candidate_count=len(items),
            caller=_current_caller(),
        )
        # 自带会话必须自己提交：service 只 flush(它平时跑在请求级会话里，
        # 由中间件统一提交)。少了这句日志写不进去，而 record 又吞掉异常 ——
        # 表会一直是空的且毫无报错。
        await db.commit()


async def _log_adoption(indicator: str, adopted: bool) -> None:
    """取数成败回填采纳信号。

    ★ agent 拿某个 code 真去取数并成功 = 它认可了检索结果。这是最可靠的正例信号，
    且**不需要额外 UI** —— 参考方案 §6 的「采纳日志」靠这个动作就能运转。
    失败不回填正例（校验拒绝的错误指标不该变成别名素材）。

    回填对象：该 code 最近一条尚无采纳结论的日志。按时间倒序取一条，
    不批量刷 —— 历史日志的采纳状态属于当时那次问答，不该被后来的取数追认。
    """
    from sqlalchemy import select

    from ..querylog.model import SemQueryLogModel
    from ..querylog.service import QueryLogService

    async with async_db_session() as db:
        row = (await db.execute(
            select(SemQueryLogModel)
            .where(
                SemQueryLogModel.matched_code == indicator,
                SemQueryLogModel.adopted.is_(None),
                SemQueryLogModel.is_deleted.is_(False),
            )
            .order_by(SemQueryLogModel.id.desc())
            .limit(1)
        )).scalars().first()
        if row is None:
            return
        await QueryLogService(
            AuthSchema(db=db, check_data_scope=False)
        ).mark_adopted(row.id, adopted=adopted, adopted_code=indicator if adopted else None)
        await db.commit()  # 同 _log_search：自带会话须自己提交


async def _do_hydrate(model_id: int, codes: dict[str, list[str]]) -> dict:
    from .hydrate import SemanticHydrator
    from .visibility import is_model_visible

    async with async_db_session() as db:
        auth = await build_mcp_auth(db)
        # ★ 三入口收口的最后一处: agent 正常拿不到无权模型的 model_id(list_models 与
        # search_semantic 都已过滤), 但**硬猜一个 id 就能读到完整口径定义** ——
        # 指标定义、权威 SQL、可用维度、口径知识。同一类缺口的残余, 不该留着。
        if not await is_model_visible(auth, model_id):
            return {
                "ok": False,
                "errors": [{
                    "rule": "model_forbidden",
                    "message": "无权访问该语义模型。这不是参数错误，换 code 重试无用；"
                               "请联系模型负责人申请授权。",
                    "retryable": False,
                }],
            }
        result = await SemanticHydrator(auth).hydrate(
            model_id,
            indicator_codes=codes.get("indicators"),
            dimension_codes=codes.get("dimensions"),
            entity_codes=codes.get("entities"),
            knowledge_keys=codes.get("knowledge"),
        )
    return {"ok": result.error is None, **_hydrate_payload(result)}


def _hydrate_payload(result) -> dict:
    return {
        "model": {
            "code": result.model_code, "name": result.model_name,
            "description": result.description, "version": result.version,
            "default_time_grain": result.default_time_grain,
        },
        "entities": result.entities,
        "dimensions": result.dimensions,
        "indicators": result.indicators,
        "joins": result.joins,
        "knowledge": result.knowledge,
        "not_found": result.not_found,
        "error": result.error,
    }


async def _do_query(model_id: int, payload: dict, max_rows: int, draft_hint: dict | None = None):
    """校验 → 填参 → 执行。上下文取自最新 Release 快照（权威口径）。"""
    from .hydrate import SemanticHydrator
    from .pipeline import QueryPipeline

    async with async_db_session() as db:
        auth = await build_mcp_auth(db)
        # ★ 取数前校验可见性: 越权报 model_forbidden 而非"不存在/未发布" ——
        # 后两者会让 agent 去猜别的 code, 或让运维去查一个不存在的部署问题
        from .visibility import is_model_visible

        if not await is_model_visible(auth, model_id):
            from .pipeline import QueryOutcome

            return QueryOutcome(ok=False, errors=[{
                "rule": "model_forbidden",
                "message": "无权访问该语义模型。这不是参数错误，换指标 code 重试无用；"
                           "请联系模型负责人申请授权。",
                "retryable": False,
            }])
        hydrated = await SemanticHydrator(auth).hydrate(
            model_id,
            indicator_codes=[payload["indicator"]] if payload.get("indicator") else None,
            dimension_codes=list(payload.get("dimensions") or []),
        )
    if hydrated.error:
        # ★ 此前这里丢弃了 hydrated.error, 于是空快照让 validator 报 indicator_exists
        # → agent 对用户说"指标不存在", 把**部署问题**归因为用户输错 code(它会去猜
        # 别的 code, 猜一万次也不会对)。全新部署尚未发布模型时这是必经路径。
        from .pipeline import QueryOutcome

        return QueryOutcome(ok=False, errors=[{
            "rule": "model_not_released",
            "message": f"{hydrated.error}。这是平台侧问题(模型尚未发布), 不是你的参数错误"
                       f" —— 请告知用户该模型未发布, 不要改指标 code 重试。",
            "retryable": False,
        }])

    context = {**_hydrate_payload(hydrated)["model"], **_hydrate_payload(hydrated)}
    context["model_code"] = hydrated.model_code
    context["version"] = hydrated.version

    pipeline = QueryPipeline(
        context_loader=lambda: context, connection=_build_connection()
    )
    return await pipeline.run(payload, max_rows=max_rows, draft_hint=draft_hint)


def _build_connection():
    """构造只读数仓连接。凭据取自 catalog 数据源配置。"""
    from .warehouse import build_readonly_connection

    missing = [
        name for name, value in (
            ("SEM_QUERY_HOST", settings.SEM_QUERY_HOST),
            ("SEM_QUERY_USER", settings.SEM_QUERY_USER),
        ) if not value
    ]
    if missing:
        # 预检而非等驱动报错: 后者给 agent 的是 (2003, "Can't connect to MySQL
        # server on ''"), 它会当成参数问题反复重试
        raise NotConfiguredError(f"数仓查询连接未配置({', '.join(missing)})")

    return build_readonly_connection(timeout=settings.SEM_QUERY_TIMEOUT)


# ---- 对外工具（docstring 即 LLM 看到的说明）----


async def search_semantic(
    query: str,
    model_id: int | None = None,
    top_k: int = 5,
    node_types: list[str] | None = None,
) -> dict[str, Any]:
    """在语义模型里搜索指标、维度、知识等资产，返回候选及其相关性分数。

    什么时候用：用户问了一个业务问题，你需要先确定他说的是哪个指标/维度。
    不要凭记忆猜指标 code —— 必须先搜。

    参数:
        query: 用户的原话或关键词（如"6月订阅收入""升级单"）。含时间词也没关系，
               会自动剥离并在 time_range 里返回。
        model_id: 限定某个语义模型；不确定就留空。
        top_k: 返回候选数，默认 5。
        node_types: 限定类型，可选 indicator / dimension / knowledge / entity / field。

    返回:
        items: 候选列表，每项含 code / name / score / match。
               **needs_clarify=true** 表示该指标口径待确认，你应向用户澄清而非直接取数；
               **not_buildable=true** 表示本模型算不了它，你应说明原因与正确出处，不要编造。
        time_range: 从问题里剥出的时间窗，取数时直接用。
        ambiguous: 多个候选难分时给出，此时应让用户用 code 明确指定。
        rejected: 没找到 —— **不在本模型覆盖范围**。如实告知用户本模型覆盖什么，
                  did_you_mean 只是字面相近，改用前须核对口径。
        unavailable: **服务端故障或未启用**，与 rejected 语义相反。
                  出现它时绝不要说"本服务不覆盖该指标" —— 那是假话；
                  应告知用户"检索服务暂时不可用"，retryable=true 才建议稍后重试。
    """
    try:
        payload = await _do_search(query, model_id, top_k, node_types)
    except Exception as e:  # noqa: BLE001 - 工具永不抛：agent 拿异常栈没法反思
        logger.warning(f"search_semantic 失败: {e}")
        return {"ok": False, "error": str(e), "items": []}

    # 记日志失败绝不影响检索结果 —— 旁路观测不得拖垮主链
    try:
        await _log_search(query, model_id, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"query 日志记录失败(已忽略): {e}")

    return {k: v for k, v in payload.items() if k != "keyword"}


async def get_model_context(
    model_id: int,
    indicator_codes: list[str] | None = None,
    dimension_codes: list[str] | None = None,
    knowledge_keys: list[str] | None = None,
) -> dict[str, Any]:
    """取语义模型的权威口径上下文：指标定义、口径 SQL、可用维度、物理位置、口径知识。

    什么时候用：搜到候选之后，取它的权威定义。**不要自己编 SQL** —— 口径 SQL 在这里。

    参数:
        model_id: 语义模型 ID。
        indicator_codes: 要取哪些指标的口径。
        dimension_codes: 要取哪些维度的绑定信息。
        knowledge_keys: 检索命中的知识 key。只影响 auto 档知识；always 档无条件返回。

    返回:
        indicators: 含 impls[].sql_text（权威口径，带 {start}/{end} 占位符）、
                    available_dimensions（**可用维度白名单**，超出它的拆分会被拒）、
                    business_definition（含基线数值与口径警告，解读时要引用）。
        entities: 含 physical（真实库表）与 fields（含 enum_values 合法取值）。
        knowledge: always 档口径护栏 + 命中的 auto 档。**这些是必须遵守的口径说明**，
                   解读结果时要带出来，尤其 caveat 类。
        not_found: 你点名了但模型里没有的 code。
    """
    try:
        return await _do_hydrate(model_id, {
            "indicators": indicator_codes,
            "dimensions": dimension_codes,
            "knowledge": knowledge_keys,
        })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_model_context 失败: {e}")
        return {"ok": False, "error": str(e)}


async def query_indicator(
    model_id: int,
    indicator: str,
    start: str | None = None,
    end: str | None = None,
    as_of: str | None = None,
    dimensions: list[str] | None = None,
    filters: list[dict] | None = None,
    order_by: dict | None = None,
    top_n: int | None = None,
    data_layer: str | None = None,
    max_rows: int | None = None,
    needs_clarify: bool = False,
    not_buildable: bool = False,
    hint_note: str | None = None,
) -> dict[str, Any]:
    """用权威口径取数。你不写 SQL —— 系统取已审核的口径 SQL 填入时间窗后只读执行。

    什么时候用：确定了指标、时间窗（和可选的拆分维度）之后。

    参数:
        model_id: 语义模型 ID。
        indicator: 指标 code（来自 search_semantic，不要自己编）。
        start / end: 时间窗，ISO 日期（如 2026-06-01）。绝大多数指标**必填** ——
                     缺时间窗会被拒绝（防全表扫描）。
        as_of: 时点类指标（如"当前在订用户数"）用这个代替 start/end。
        dimensions: 拆分维度的 code。必须在该指标的 available_dimensions 内，
                    否则会被拒绝并告诉你允许哪些。
                    ★ 维度**可以不在指标主实体上**：系统会按已登记的实体关系自动关联
                    一跳并重建 SQL，口径条件原样保留。但这条路有边界，会显式拒绝：
                    ①经**一对多**关系时只有去重计数类指标可用，SUM/AVG/COUNT(*) 返回
                    rule=fanout_refused（实测经此关联做 SUM 会虚增约 20%，且 SQL 与
                    EXPLAIN 都不报错）；②口径不是单层单表简单聚合（含 CTE/子查询/
                    多表/多聚合，或口径**自带 GROUP BY**）返回 rule=not_decomposable。
                    ★★ 这两种拒绝是**答案，不是重试提示** —— 它们说的是"这个数按这个
                    维度拆出来会是错的"，改参数重问一万次也不会变。照 allowed 里的建议
                    处理（换关联表那一侧的指标 / 请数据负责人补口径 / 改用主实体上的
                    维度），并如实告诉用户拆不了及原因。
                    **绝不要换个名字相近的指标凑数** —— 那个数字看起来完全合理、
                    用户无从察觉，比说"拆不了"糟糕得多。
                    ③一次只支持**一个**跨实体维度、且不能同时带 filters
                    （会返回 join_multi_dimension_unsupported /
                    join_filter_unsupported，说明该落在哪张表是业务判断，猜错不报错、
                    只会让数字变了）。
        filters: 过滤条件，形如 [{"field":"order_type","op":"=","value":"subscription"}]。
                 值必须在该字段的 enum_values 内。
        order_by: 排序规格，形如 {"field": "<排序键>", "direction": "desc"}。
                 ★ 排名类问题（"前 10 名""最大/最小的 X"）必须用它 —— 否则结果是
                 分组顺序，不保证按值排序，用 max_rows 截"前 N"会切错。
                 排序键只能二选一：**指标 code 本身**（按度量值排，最常见）或**本次
                 dimensions 里的某个维度 code**（按分组列排）。落在别处会返回
                 rule=order_by_invalid 并在 allowed 里列出可排序字段。
                 direction 取 "desc"（默认）或 "asc"。排序在权威聚合**之后**执行，
                 不改口径。
        top_n: 排序后只取前 N 行（如 10）。**必须与 order_by 同时给**（否则"前 N"
                 按什么排不确定，返回 rule=top_n_without_order）。这是业务意图截取，
                 与 max_rows（防全表扫描的护栏）不同：干净取到前 N 时 truncated=false、
                 并带 top_n_applied=true；若 N 超过行数护栏、被迫截到护栏，则
                 truncated=true（结果不完整，须向用户说明）。
                 ★ 跨实体维度暂不支持 order_by / top_n（返回 join_order_unsupported）——
                 先不排序取回聚合结果、在结果上自己排，或改按主实体维度排名。
        data_layer: 可选。你打算走哪个数据层（DWS/DWD/ADS/ODS/DIM）。
                 ★ 这是**护栏**，不是选择器 —— 它不会改变实际执行哪条口径 SQL。
                 声明后若与权威主口径所在层不符会被拒绝并告知实际层；订单级指标声明
                 走 DWD 明细层也会被拒（一订单多事件，聚合会重复计）。
                 不确定就别传，让权威口径自己决定。
        max_rows: 返回行数上限。不传则用平台配置的上限（SEM_QUERY_MAX_ROWS）；
                 结果被截断时返回 truncated=true，此时须向用户说明结果不完整。
        needs_clarify / not_buildable / hint_note: 把 search_semantic 返回的同名标记
                 原样传进来。草稿与跨域指标不在权威快照里，带上标记才能得到
                 "口径待确认"或"本模型不可算 + 出处"这类可行动的说明，
                 而不是笼统的"指标不存在"。

    返回:
        ok=true 时：rows（结果行）、executed_sql（实际执行的 SQL，可给用户看）、
                    caliber（**口径声明：指标/单位/定义/数据层/模型版本**，
                    解读结果时必须带出，否则数字脱离口径无法判断可信度；
                    ★ 若含 caliber.warnings，**必须一并转述给用户** —— 它是跨实体
                    关联取数的风险提示，如"经一对多关系取数"，不转述用户就无从
                    判断该不该信这个数）、
                    knowledge（须遵守的口径说明）、truncated（结果是否被截断）。
        ok=false 时：errors 每项含 rule / message / allowed。
                    看 allowed 改正参数后重试；带 needs_clarify 的要向用户澄清；
                    ★ rule=not_configured / model_not_released 且 retryable=false →
                    这是**服务端问题**(数仓未配 / 模型未发布)，改参数重试无用；
                    如实告知用户是平台侧问题，不要去猜别的指标 code。
                    rule=caliber_break 表示时间窗跨越口径分水岭，数据不可比 ——
                    要如实告知用户而非给出一个看似正确的数字。
                    ★ 跨实体下钻的拒绝按**该找谁**分三类，处置完全不同：
                    (a) **登记坏了、要人去修** —— join_on_missing / join_on_invalid /
                        join_type_unsupported / ambiguous_join / join_plan_failed /
                        entity_physical_missing / join_alias_conflict：
                        告知用户"该关联关系在语义模型里登记有问题"，请数据负责人核对，
                        不要重试、也不要绕路换指标。
                    (b) **这个指标/这个形状就是拆不了** —— fanout_refused /
                        not_decomposable / caliber_parse_failed / multi_hop_unsupported：
                        这是**真答案**。照 allowed 换到关联表那一侧的指标，
                        或请数据负责人补一条能按该维度拆分的口径。
                    (c) **你的请求里有不支持的东西，改请求就行** ——
                        join_filter_unsupported / join_multi_dimension_unsupported /
                        bind / join_compile_failed：按 allowed 去掉过滤、只留一个维度、
                        或改按主实体上的维度拆分，然后重试。
                    完整规则表见 backend/docs/semantic-mcp.md。
    """
    time: dict[str, str] = {}
    if start:
        time["start"] = start
    if end:
        time["end"] = end
    if as_of:
        time["as_of"] = as_of

    payload = {
        "indicator": indicator,
        "dimensions": list(dimensions or []),
        "filters": list(filters or []),
        "order_by": order_by or None,
        "top_n": top_n,
        "time": time or None,
        "data_layer": data_layer,
    }
    hint = None
    if needs_clarify or not_buildable:
        hint = {"needs_clarify": needs_clarify, "not_buildable": not_buildable,
                "note": hint_note or ""}
    try:
        # 不传时回落到平台配置(而非硬编码): 写进函数默认值会在 import 时冻结取值,
        # 使 monkeypatch 与运行时改配置都失效
        effective_rows = max_rows if max_rows is not None else settings.SEM_QUERY_MAX_ROWS
        outcome = await _do_query(model_id, payload, effective_rows, hint)
        result = outcome.to_dict()
    except NotConfiguredError as e:
        logger.warning(f"取数被拒(服务端未配置): {e}")
        return {"ok": False, "errors": [{
            "rule": "not_configured",
            "message": f"{e} —— 这是服务端配置问题，重试无用，请联系平台管理员。",
            "retryable": False,
        }]}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"query_indicator 失败: {e}")
        return {"ok": False, "error": str(e), "errors": [{"rule": "internal", "message": str(e)}]}

    # 取数成功 = agent 认可了检索给的这个 code（§6 采纳信号，无需额外 UI）
    try:
        await _log_adoption(indicator, adopted=bool(result.get("ok")))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"采纳信号回填失败(已忽略): {e}")

    return result


async def execute_sql(sql: str, max_rows: int | None = None) -> dict[str, Any]:
    """执行你自己写的只读 SQL（探索与核对用）。**报数字给用户请优先用 query_indicator。**

    什么时候用：
    - 探索性核对（"这张表最近有数据吗""这个字段实际有哪些取值"）
    - 权威口径覆盖不到的临时切片
    - query_indicator 明确拒绝了（fanout_refused / not_decomposable），
      而你确实需要看一眼底层数据

    ★★ 什么时候**不要**用：用户要一个业务数字（收入/用户数/订单量……）时。
    那种情况必须走 query_indicator —— 它返回的数带**口径声明**（caliber），
    本工具返回的数**没有任何口径背书**：你自己写的 SQL 没人审核过，可能与公司口径
    不一致（去重方式、时间归属、汇率、排除测试账号……都是口径决策）。
    用本工具的结果冒充权威数字，是最糟的失败方式 —— 数字看起来完全合理，
    用户无从察觉。**如实说明"这是我直接查底表得到的，未经口径审核"。**

    参数:
        sql: 只读 SQL。仅允许 SELECT / WITH；含任何写操作关键字、多语句会被拒。
             缺 LIMIT 会自动加（防全表拉取）。方言见返回的 dialect 字段。
        max_rows: 返回行数上限。不传用平台配置（SEM_QUERY_MAX_ROWS）。

    返回:
        ok=true 时：data（结果行，**每行是数组**、值按 columns 逐位对齐，
                    如 columns=["a","b"] 时 data=[[1,"x"],[2,"y"]]）、
                    columns（列名，**0 行时也有** ——
                    columns 非空而 data 为空即"该条件下确实没有数据"，
                    不要以为自己写错了列名去猜着重试）、row_count、
                    truncated（true = 结果被截断、不完整，须向用户说明）、
                    duration_ms（查询本身耗时，不含守卫/EXPLAIN/审计写入）、
                    executed_sql（实际执行的，含自动加的 LIMIT）、dialect。
        ok=false 时：error + error_type：
                    - permission_denied：守卫拒绝（写操作/多语句）。改成只读查询。
                    - syntax_error：EXPLAIN 没过。按 error 里的库端报错改 SQL；
                      注意方言是 dialect 字段那个，别用别家语法。
                    - execution_failed：真跑失败（超时等）。可缩小时间窗/加过滤重试。
                    - not_configured：**服务端未配数仓连接**。改 SQL 重试无用，
                      如实告知用户这是平台侧配置问题。
    """
    import time

    from ..rawsqllog import service as _audit
    from .raw_query import run_raw_sql

    caller = _current_caller()

    async def _audit_log(**kwargs) -> None:
        """记审计。★ 旁路：既不得让取数失败，也不得让取数变慢/丢结果。

        经模块属性调用（`_audit.log_raw_sql`）而非直接 import 进来：
        测试要能替换它，且这样能钉住"确实走了那个函数"。

        这里挡三件事，各有各的失败方向：

        1. **异常** —— 吞掉。让观测把一次成功的取数变成失败是本末倒置。

        2. **延迟** —— 加超时（`_AUDIT_TIMEOUT_SECONDS`）。"审计不得让取数失败"对异常
           成立、对延迟原本不成立：`log_raw_sql` 自己开 app 库会话，库病了光开会话就
           10s、池打满 30s、单纯慢则没有上限。丢掉的行记 WARNING —— 那是**审计缺口**，
           得有人能看见它发生过，静默丢行是这张表最坏的失败方向。

        3. **取消** —— 吞掉这一次，再挂回当前任务。这个 await 是 `run_raw_sql` 返回
           **之后**新增的挂起点（接线前那两句之间没有 await），而 `CancelledError` 是
           `BaseException`，`except Exception` 接不住：客户端断开/上游超时恰好落在审计里
           时，一次**已经成功、数仓已经付过账**的查询会整个作废，调用方什么都拿不到。

           为什么不是别的写法：
           - `except BaseException` 会把取消**永久**吃掉，任务从此不可取消（上游超时、
             优雅关停都拿它没办法）—— 换了个方向的同级错误。
           - `asyncio.shield` 只保住审计那个协程不被取消，`CancelledError` 照旧在这里
             抛出来，结果一样被丢掉 —— 它解决的不是这个问题。
           所以：吞下这次取消让 `result` 安全交回，同时把取消重新挂回当前任务，
           它会在调用方**下一个挂起点**投递。结果不丢，任务照旧可取消。

           ★ 重挂时必须 `uncancel()` 再 `cancel()`，不能只 `cancel()`：光 `cancel()` 会让
           `task.cancelling()` 净增 1，而 `asyncio.timeout.__aexit__` 靠
           `task.uncancel() <= self._cancelling` 判断"这次取消是不是我发的"。计数不平，
           上游那层 `asyncio.timeout` 就认不出自己的取消、不再转成 `TimeoutError` ——
           调用方本该收到 `TimeoutError` 却收到 `CancelledError`（实测复现过）。
           那正是本次改动最该避免的东西：修一个行为变化时引入另一个。
           `uncancel()+cancel()` 计数净变 0，只把"待投递"重新武装起来。

           只对 `CancelledError` 网开一面：`KeyboardInterrupt` / `SystemExit` 仍原样抛出，
           否则等于进程收到停止信号还继续跑。
        """
        try:
            async with asyncio.timeout(_AUDIT_TIMEOUT_SECONDS):
                await _audit.log_raw_sql(raw_sql=sql, caller=caller, **kwargs)
        except TimeoutError:
            # ★ WARNING 而非 debug：这是一条审计缺口，不是一次无关紧要的重试
            logger.warning(
                f"裸 SQL 审计超时({_AUDIT_TIMEOUT_SECONDS}s)已放弃 —— "
                f"本次调用没有审计记录(审计缺口)，取数结果照常返回。"
                f"请检查 app 库连通性/连接池。caller={caller}"
            )
        except asyncio.CancelledError:
            # 取消落在审计里：先让已到手的结果交回去，再把取消挂回当前任务。
            # uncancel() 抵掉本次取消的计数，cancel() 重新武装 —— 净变 0，见 docstring
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
                task.cancel()
            logger.warning(
                f"裸 SQL 审计期间调用被取消(如客户端断开) —— 本次调用没有审计记录"
                f"(审计缺口)，但已完成的取数结果不丢弃。caller={caller}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"裸 SQL 审计失败(已忽略, 不影响取数): {e}")

    # ★★ 默认关闭。这条路能读只读账号可见的**任何**表（含 restricted 模型背后的
    # 物理表、以及没进任何语义模型的表），访问面比主链路大得多 —— 主链路只跑已审核
    # 口径 SQL 且过 is_model_visible。默认开启会让既有部署一升级就静默放宽边界。
    if not settings.SEM_RAW_SQL_ENABLE:
        # 关闭态也留痕：能看出有人在试图用这条路（说明该考虑要不要开它）
        await _audit_log(ok=False, error_type="not_configured")
        return {
            "ok": False,
            "error": "本部署未启用裸 SQL 执行（SEM_RAW_SQL_ENABLE）。这是平台侧的"
                     "部署选择，不是你的 SQL 写错了 —— 改 SQL 重试无用。"
                     "请改用 query_indicator 走权威口径，或联系管理员。",
            "error_type": "not_configured",
            "retryable": False,
        }

    try:
        connection = _build_connection()
    except NotConfiguredError as e:
        logger.warning(f"裸 SQL 执行被拒(服务端未配置): {e}")
        await _audit_log(ok=False, error_type="not_configured", error=str(e))
        return {
            "ok": False,
            "error": f"{e} —— 这是服务端配置问题，重试无用，请联系平台管理员。",
            "error_type": "not_configured",
            "retryable": False,
        }

    # 不传时回落平台配置(而非硬编码): 写进函数默认值会在 import 时冻结取值,
    # 使 monkeypatch 与运行时改配置都失效(query_indicator 为此修过一次)
    effective_rows = max_rows if max_rows is not None else settings.SEM_QUERY_MAX_ROWS
    # duration_ms 的窗口从这里到 run_raw_sql 返回：**含** 建 TCP 连接 + EXPLAIN 预检
    # + 真正的查询（`_build_connection()` 只组 URL 不连网，首次 connect 发生在 explain()
    # 里、在窗口内）；**不含** 守卫/解析、以及审计自身的写入耗时。即"这条 SQL 在数仓上
    # 花了多久"，不是"这次工具调用花了多久"。
    started = time.monotonic()
    try:
        result = await run_raw_sql(
            sql,
            connection=connection,
            max_rows=effective_rows,
            timeout=settings.SEM_QUERY_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 - 工具永不抛：agent 拿异常栈没法反思
        logger.warning(f"execute_sql 失败: {e}")
        await _audit_log(
            ok=False, error_type="execution_failed", error=str(e),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return {"ok": False, "error": str(e), "error_type": "execution_failed"}

    await _audit_log(
        ok=bool(result.get("ok")),
        executed_sql=result.get("executed_sql"),
        error_type=result.get("error_type"),
        error=result.get("error"),
        row_count=result.get("row_count"),
        truncated=bool(result.get("truncated")),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result


def semantic_tools() -> list:
    """供 Agno Agent 使用的工具列表。"""
    return [search_semantic, get_model_context, query_indicator, execute_sql]
