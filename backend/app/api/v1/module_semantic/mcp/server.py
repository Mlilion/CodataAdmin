"""MCP server：三个工具 + Streamable HTTP 传输。

★ `description` 是外部 agent **唯一**能看到的说明 —— 它不是注释，是接口契约的一部分。
写不清"什么时候用、needs_clarify 该怎么处理"，工具接上了也会被用错。

传输用 Streamable HTTP + `stateless_http=True`（多 worker 安全）。
"""

import json
import re
from typing import Any

from fastmcp import FastMCP

from app.core.logger import logger

from ..retrieval.tools import execute_sql as _raw_sql
from ..retrieval.tools import get_model_context as _context
from ..retrieval.tools import query_indicator as _query
from ..retrieval.tools import search_semantic as _search

_INT_TEXT = re.compile(r"^\d+$")

mcp = FastMCP(
    name="CodataAdmin Semantic",
    instructions=(
        "本服务提供语义模型的检索、权威口径召回与取数能力。\n\n"
        "标准流程：list_models 拿到 model_id → search_semantic 找到指标 code → "
        "get_model_context 取权威口径 → query_indicator 取数。\n\n"
        "三条铁律：\n"
        "0. 不要猜 model_id —— 其余工具都要求它必填，先调 list_models；\n"
        "1. 不要凭记忆猜指标 code，必须先 search_semantic；\n"
        "2. **报业务数字不要自己写 SQL** —— 口径 SQL 由本服务从已审核的权威口径取得"
        "并填参执行。另有 execute_sql 可执行你自己写的只读 SQL，但那是**探索/核对**"
        "用的旁路：它的结果没有口径背书，不得用来回答\"收入是多少\"这类问题，"
        "用了必须明说\"这是直接查底表得到的、未经口径审核\"。\n\n"
        "★★ 搜不到时怎么办（本服务目前只覆盖商业化营收域，问到域外是常态）：\n"
        "- 先看 list_models 的模型说明，判断用户问的是否在覆盖范围内；\n"
        "- **不在范围内** → 如实说\"本服务只覆盖<某域>，你问的<某指标>不在其中\"，"
        "并说明该去哪里查。这是正确回答，不是失败；\n"
        "- **not_buildable=true** → 本模型算不了它，说明原因与正确出处；\n"
        "- **did_you_mean 有建议** → 那只是**字面相近**，不代表口径相同"
        "（如 arpu 会建议 arppu，两者分母是活跃用户 vs 付费用户）。"
        "改用前必须 get_model_context 核对口径定义；\n"
        "- ★ **绝不用相近指标凑数**：拿口径不符的指标返回一个数字，"
        "比说\"没有\"糟糕得多 —— 数字看起来合理，用户无从察觉，会拿它去做决策。\n\n"
        "报告数字时必须带出返回的 caliber 口径声明与 caveat 类知识，"
        "否则数字脱离口径无法判断可信度。"
    ),
)


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(exc: Exception, **extra) -> str:
    """故障也返回 JSON —— MCP client 拿到异常栈没法反思。"""
    logger.warning(f"MCP 工具执行失败: {exc}")
    return _dump({"ok": False, "error": str(exc), **extra})


def _coerce_int_arg(value: int | str | None, *, field: str, allow_none: bool = False) -> int | None:
    """把客户端偶发串化的整数参数拉回 int。

    ★ MCP/LLM 边界偶尔会把 `1` 变成 `"1"`。这类值应当兼容，
    但像 `"demo_finance_analysis"` 这种真错值仍然要明确报出。
    """
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field} 不能为空")
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是整数")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _INT_TEXT.fullmatch(text):
            return int(text)
    raise ValueError(f"{field} 必须是整数，当前收到 {value!r}")


async def _list_released_models() -> list[dict]:
    """列出有发布快照的模型及其最新版本。

    只列**已发布**的：未发布模型没有快照，agent 选了之后 get_model_context /
    query_indicator 必然失败 —— 把它列出来等于给一个会踩空的选项。
    """
    from sqlalchemy import func, select

    from app.core.database import async_db_session

    from ..model.model import SemModelModel, SemModelReleaseModel
    from ..retrieval.tools import build_mcp_auth
    from ..retrieval.visibility import visible_model_ids

    async with async_db_session() as db:
        auth = await build_mcp_auth(db)
        # ★ 必须在这里就过滤: 列出无权的 model_id 等于给 agent 一个会被拒的选项,
        # 且泄露了"平台上存在哪些模型"这一信息
        allowed = await visible_model_ids(auth)
        # 每个模型取最新 version
        latest = (
            select(
                SemModelReleaseModel.model_id.label("mid"),
                func.max(SemModelReleaseModel.version).label("ver"),
            )
            .where(SemModelReleaseModel.is_deleted.is_(False))
            .group_by(SemModelReleaseModel.model_id)
            .subquery()
        )
        rows = (await db.execute(
            select(
                SemModelModel.id, SemModelModel.code, SemModelModel.name,
                SemModelModel.description, latest.c.ver,
            )
            .join(latest, latest.c.mid == SemModelModel.id)
            .where(
                SemModelModel.is_deleted.is_(False),
                *([SemModelModel.id.in_(allowed)] if allowed is not None else []),
            )
            .order_by(SemModelModel.id)
        )).all()

    return [
        {"id": r[0], "code": r[1], "name": r[2], "description": r[3], "version": r[4]}
        for r in rows
    ]


@mcp.tool(
    description=(
        "列出本平台所有**已发布**的语义模型及其 model_id。\n\n"
        "【什么时候用】会话开始、你还不知道有哪些模型时的**第一个调用**。"
        "其余工具都要求 model_id 必填，而你无法凭猜测得到它 —— 先调本工具。\n\n"
        "【参数】无。\n\n"
        "【返回】models[]，每项含：\n"
        "- id: 传给 search_semantic / get_model_context / query_indicator 的 model_id\n"
        "- code / name / description: 用于判断用户的问题属于哪个模型"
        "（如营收类问题选 commercial_revenue）\n"
        "- version: 当前发布版本号，报告数字时可一并说明口径版本\n\n"
        "【注意】只列已发布的模型。未发布的没有权威快照，取数会失败。"
        "若返回空列表，说明平台还没有发布任何语义模型，应如实告知用户而非猜测。"
    )
)
async def list_models() -> str:
    """列出已发布的语义模型（agent 发现 model_id 的入口）。"""
    try:
        return _dump({"ok": True, "models": await _list_released_models()})
    except Exception as e:  # noqa: BLE001
        return _error(e)


@mcp.tool(
    description=(
        "在语义模型里搜索指标、维度、知识等资产，返回候选及其相关性分数。\n\n"
        "【什么时候用】用户问了业务数据问题，你需要先确定他说的是哪个指标或维度。"
        "**不要凭记忆猜指标 code，必须先调本工具。**\n\n"
        "【参数】\n"
        "- query: 用户原话或关键词（如「6月订阅收入」「升级单」）。含时间词也没关系，"
        "会自动剥离并在 time_range 里返回。\n"
        "- model_id: 限定某个语义模型；不确定就留空。可传整数或数字字符串，服务端会归一化。\n"
        "- top_k: 返回候选数，默认 5；可传数字字符串，服务端会归一化。\n"
        "- node_types: 限定类型，可选 indicator / dimension / knowledge / entity / field。\n\n"
        "【返回】\n"
        "- items[]: 每项含 code / name / score / match。\n"
        "  ★ **needs_clarify=true** → 该指标口径待确认，应先向用户澄清口径选择，"
        "不要直接取数；取数时把这个标记原样传给 query_indicator。\n"
        "  ★ **not_buildable=true** → 本模型算不了它，应如实说明原因与正确出处，"
        "**绝不用其他指标凑近似值，也不要编造**。\n"
        "- time_range: 从问题里剥出的时间窗，取数时直接用。\n"
        "- ambiguous: 多个候选难分时给出，此时让用户用 code 明确指定，不要替他猜。\n"
        "- ★ unavailable 非空 → 这是**服务端故障或未启用**，不是覆盖范围问题。"
        "应告知用户「检索服务暂时不可用」并按 retryable 决定是否建议稍后重试，"
        "**绝不要**说「本服务不覆盖该指标」—— 那是假话。\n"
        "- rejected: 没找到。★ items 为空是**有意义的答案**（问题不在本模型覆盖范围），"
        "不是工具故障 —— 应如实告知用户本模型覆盖什么、他问的不在其中，"
        "而不是降级去猜一个相近指标。did_you_mean 只是字面相近的候选，"
        "改用前须 get_model_context 核对口径是否真的等价。"
    )
)
async def search_semantic(
    query: str,
    model_id: int | str | None = None,
    top_k: int | str = 5,
    node_types: list[str] | None = None,
) -> str:
    """检索语义资产。"""
    try:
        normalized_model_id = _coerce_int_arg(model_id, field="model_id", allow_none=True)
        normalized_top_k = _coerce_int_arg(top_k, field="top_k")
        return _dump(await _search(
            query, model_id=normalized_model_id, top_k=normalized_top_k, node_types=node_types
        ))
    except ValueError as e:
        return _error(e, items=[])
    except Exception as e:  # noqa: BLE001
        return _error(e, items=[])


@mcp.tool(
    description=(
        "取语义模型的权威口径上下文：指标定义、口径 SQL、可用维度白名单、"
        "物理库表位置、口径知识。\n\n"
        "【什么时候用】search_semantic 找到候选之后，取它的权威定义。"
        "**不要自己编 SQL —— 口径 SQL 就在这里。**\n\n"
        "【参数】\n"
        "- model_id: 语义模型 ID（可传整数或数字字符串，服务端会归一化）。\n"
        "- indicator_codes / dimension_codes: 要取哪些资产的口径。\n"
        "- knowledge_keys: 检索命中的知识 key。只影响 auto 档；always 档无条件返回。\n\n"
        "【返回】\n"
        "- indicators[]: 含 impls[].sql_text（权威口径，带 {start}/{end} 占位符）、"
        "available_dimensions（**可用维度白名单**，超出它的拆分会被拒绝）、"
        "business_definition（含基线数值与口径警告，解读时要引用）、lifecycle_status。\n"
        "- entities[]: 含 physical（真实库表）与 fields（含 enum_values 合法取值，"
        "过滤时只能用这些值）。\n"
        "- knowledge[]: always 档口径护栏 + 命中的 auto 档。"
        "★ **这些是必须遵守的口径说明，解读结果时要一并告知用户，尤其 caveat 类。**\n"
        "- not_found[]: 你点名了但模型里没有的 code。"
    )
)
async def get_model_context(
    model_id: int | str,
    indicator_codes: list[str] | None = None,
    dimension_codes: list[str] | None = None,
    knowledge_keys: list[str] | None = None,
) -> str:
    """取权威口径上下文。"""
    try:
        normalized_model_id = _coerce_int_arg(model_id, field="model_id")
        return _dump(await _context(
            normalized_model_id,
            indicator_codes=indicator_codes,
            dimension_codes=dimension_codes,
            knowledge_keys=knowledge_keys,
        ))
    except ValueError as e:
        return _error(e)
    except Exception as e:  # noqa: BLE001
        return _error(e)


@mcp.tool(
    description=(
        "用权威口径取数。**你不写 SQL** —— 本服务取已审核的口径 SQL，填入时间窗后"
        "只读执行（自动加 LIMIT、先 EXPLAIN 预检）。\n\n"
        "【什么时候用】确定了指标、时间窗（和可选的拆分维度）之后。\n\n"
        "【参数】\n"
        "- model_id: 语义模型 ID（可传整数或数字字符串，服务端会归一化）。\n"
        "- indicator: 指标 code 来自 search_semantic，不要自己编。\n"
        "- start / end: 时间窗，ISO 日期（如 2026-06-01）。绝大多数指标**必填** —— "
        "缺时间窗会被拒绝（防全表扫描）。用户没说时间就问他，不要默认查全部。\n"
        "- as_of: 时点类指标（如「当前在订用户数」）用这个代替 start/end。\n"
        "- dimensions: 拆分维度 code，必须在该指标的 available_dimensions 内。\n"
        "  ★ 维度**可以不在指标主实体上**：本服务会按已登记的实体关系自动关联一跳并"
        "重建 SQL，口径条件原样保留。但这条路有边界，会显式拒绝而不猜：经**一对多**"
        "关系时只有去重计数类指标可用（SUM/AVG/COUNT(*) 返回 fanout_refused —— "
        "实测经此关联做 SUM 会虚增约 20%，而 SQL 与 EXPLAIN 都不报错）；"
        "口径不是单层单表简单聚合（含 CTE/子查询/多表/多聚合，或口径**自带 GROUP BY**）"
        "返回 not_decomposable；一次只支持一个跨实体维度且不能同时带 filters。\n"
        "  ★★ fanout_refused / not_decomposable 是**答案，不是重试提示** —— 它们说的是"
        "「这个数按这个维度拆出来会是错的」，改参数重问多少次都不会变。"
        "**绝不要换个名字相近的指标凑数**：那个数字看起来完全合理、用户无从察觉，"
        "比如实说「拆不了」糟糕得多。\n"
        "- filters: 形如 [{\"field\":\"order_type\",\"op\":\"=\",\"value\":\"subscription\"}]，"
        "值必须在该字段的 enum_values 内。\n"
        "- order_by: 形如 {\"field\":\"<排序键>\",\"direction\":\"desc\"}。★ 排名类问题"
        "（「前 10 名」「最大/最小的 X」）**必须**用它 —— 否则返回的是分组顺序，"
        "不保证按值排序，用 max_rows 截「前 N」会切错。排序键只能二选一：**指标 code "
        "本身**（按度量值排，最常见）或**本次 dimensions 里的维度 code**（按分组列排）；"
        "落在别处返回 order_by_invalid 并列出可排序字段。direction 取 desc（默认）/asc。"
        "排序在权威聚合之后执行，不改口径。\n"
        "- top_n: 排序后只取前 N 行。**必须与 order_by 同时给**（否则返回 "
        "top_n_without_order）。这是业务意图截取，与 max_rows（防扫描护栏）不同：干净取到"
        "前 N 时 truncated=false 且带 top_n_applied=true；若 N 超过行数护栏被迫截断则 "
        "truncated=true。★ 跨实体维度暂不支持 order_by/top_n（返回 join_order_unsupported），"
        "改按主实体维度排名或先取回再自行排序。\n"
        "- data_layer: 可选，你打算走哪个数据层（DWS/DWD/ADS/ODS/DIM）。"
        "★ 这是**护栏**不是选择器 —— 它不会改变实际执行哪条口径 SQL；声明后若与权威"
        "主口径所在层不符会被拒绝，订单级指标声明走 DWD 明细层也会被拒"
        "（一订单多事件，聚合会重复计）。不确定就别传。\n"
        "- needs_clarify / not_buildable / hint_note: 把 search_semantic 返回的同名标记"
        "原样传进来。草稿与跨域指标不在权威快照里，带上标记才能得到「口径待确认」或"
        "「本模型不可算 + 出处」这类可行动的说明，而不是笼统的「指标不存在」。\n\n"
        "【返回 ok=true】\n"
        "- rows / executed_sql（实际执行的 SQL，可给用户看）\n"
        "- ★ caliber: **口径声明**（指标全名/单位/口径定义/数据层/模型版本）。"
        "报告数字时必须带出，否则数字脱离口径无法判断可信度。\n"
        "- ★ caliber.warnings（有则必须一并转述）: 跨实体关联取数的风险提示，"
        "如「经一对多关系取数」。它是这次放行的唯一留痕，不转述用户就无从判断"
        "该不该信这个数。\n"
        "- knowledge: 须遵守并告知用户的口径说明\n"
        "- truncated: true 表示结果被截断、不完整，要说明\n"
        "- top_n_applied: true 表示已按你的 top_n 取了前 N 行（这是意图截取，非「不完整」）\n\n"
        "【返回 ok=false】errors[] 每项含 rule / message / allowed：\n"
        "- 按 allowed 改正参数后重试\n"
        "- needs_clarify=true → 向用户澄清，不要自行决定\n"
        "- rule=caliber_break → 时间窗跨越口径分水岭、数据不可比，"
        "**如实告知用户，不要给出一个看似正确的数字**\n"
        "- rule=not_buildable → 说明本模型算不了它及正确出处，不要编造\n"
        "- ★ rule=not_configured / model_not_released(retryable=false) → **服务端问题**"
        "(数仓连接未配置 / 该模型尚未发布)。改参数或换指标 code 重试**无用**，"
        "应如实告知用户这是平台侧配置问题，请联系管理员\n"
        "- ★ 跨实体下钻的拒绝按**该找谁**分三类：\n"
        "  (a) 登记坏了、要人去修 —— join_on_missing / join_on_invalid / "
        "join_type_unsupported / ambiguous_join / join_plan_failed / "
        "entity_physical_missing / join_alias_conflict：告知用户该关联关系在语义模型里"
        "登记有问题，请数据负责人核对；不要重试，也不要绕路换指标。\n"
        "  (b) 这个指标/这个形状就是拆不了 —— fanout_refused / not_decomposable / "
        "caliber_parse_failed / multi_hop_unsupported：这是**真答案**，"
        "照 allowed 换到关联表那一侧的指标，或请数据负责人补一条能按该维度拆分的口径。\n"
        "  (c) 请求里有不支持的东西、改请求就行 —— join_filter_unsupported / "
        "join_multi_dimension_unsupported / bind / join_compile_failed：按 allowed "
        "去掉过滤、只留一个维度，或改按主实体上的维度拆分，然后重试。"
    )
)
async def query_indicator(
    model_id: int | str,
    indicator: str,
    start: str | None = None,
    end: str | None = None,
    as_of: str | None = None,
    dimensions: list[str] | None = None,
    filters: list[dict] | None = None,
    order_by: dict | None = None,
    top_n: int | str | None = None,
    data_layer: str | None = None,
    max_rows: int | str = 500,
    needs_clarify: bool = False,
    not_buildable: bool = False,
    hint_note: str | None = None,
) -> str:
    """用权威口径取数。"""
    try:
        normalized_model_id = _coerce_int_arg(model_id, field="model_id")
        normalized_max_rows = _coerce_int_arg(max_rows, field="max_rows")
        normalized_top_n = _coerce_int_arg(top_n, field="top_n", allow_none=True)
        return _dump(await _query(
            model_id=normalized_model_id,
            indicator=indicator,
            start=start,
            end=end,
            as_of=as_of,
            dimensions=dimensions,
            filters=filters,
            order_by=order_by,
            top_n=normalized_top_n,
            data_layer=data_layer,
            max_rows=normalized_max_rows,
            needs_clarify=needs_clarify,
            not_buildable=not_buildable,
            hint_note=hint_note,
        ))
    except ValueError as e:
        return _error(e, errors=[{"rule": "invalid_argument", "message": str(e)}])
    except Exception as e:  # noqa: BLE001
        return _error(e, errors=[{"rule": "internal", "message": str(e)}])


@mcp.tool(
    description=(
        "执行**你自己写的**只读 SQL。仅供探索与核对 —— "
        "**报业务数字请用 query_indicator。**\n\n"
        "【什么时候用】\n"
        "- 探索性核对：这张表最近有数据吗、这个字段实际有哪些取值、行数量级对不对\n"
        "- 权威口径覆盖不到的临时切片\n"
        "- query_indicator 拒绝了（fanout_refused / not_decomposable），"
        "而你确实需要看一眼底层数据长什么样\n\n"
        "【什么时候绝对不要用】★★\n"
        "用户要一个业务数字（收入 / 用户数 / 订单量 / 转化率……）时必须走 "
        "query_indicator。本工具的结果**没有口径背书**：SQL 是你写的，没人审核过，"
        "可能与公司口径不一致 —— 去重方式、时间归属、汇率、是否排除测试账号与退款，"
        "全都是**口径决策**，不是 SQL 技巧。\n"
        "拿本工具的数冒充权威数字是最糟的失败方式：数字看起来完全合理，用户无从察觉，"
        "却会拿它去做决策。若确实只能用本工具，**必须如实说明"
        "「这是我直接查底表得到的，未经口径审核」**。\n\n"
        "【参数】\n"
        "- sql: 只读 SQL。仅允许 SELECT / WITH；写操作关键字与多语句一律拒绝；"
        "缺 LIMIT 会自动补（防全表拉取）。方言见返回的 dialect 字段，"
        "别用别家语法。\n"
        "- max_rows: 行数上限，不传用平台配置；可传数字字符串，服务端会归一化。\n\n"
        "【返回 ok=true】\n"
        "- data: 结果行。**每行是数组**（值按 columns 逐位对齐），列名只在 columns 里，"
        "如 columns=[\"a\",\"b\"] 时 data=[[1,\"x\"],[2,\"y\"]]\n"
        "- ★ columns: 列名，**0 行时也有**。columns 非空而 data 为空 = "
        "「该条件下确实没有数据」，这是**结论**，不要以为自己写错列名去猜着重试\n"
        "- row_count / truncated（true = 被截断、结果不完整，须向用户说明）\n"
        "- duration_ms: 查询本身耗时（不含守卫/EXPLAIN/审计写入）\n"
        "- executed_sql（含自动补的 LIMIT）/ dialect\n\n"
        "【返回 ok=false】看 error_type：\n"
        "- permission_denied: 守卫拒绝（写操作 / 多语句）。改成只读查询。\n"
        "- syntax_error: EXPLAIN 没过。按 error 里的库端报错改；注意方言。\n"
        "- execution_failed: 真跑失败（如超时）。缩小时间窗或加过滤重试。\n"
        "- not_configured: **服务端未配数仓连接**。改 SQL 重试无用，"
        "如实告知用户这是平台侧配置问题，请联系管理员。"
    )
)
async def execute_sql(sql: str, max_rows: int | str | None = None) -> str:
    """执行只读 SQL（探索用，结果无口径背书）。"""
    try:
        normalized_max_rows = _coerce_int_arg(max_rows, field="max_rows", allow_none=True)
        return _dump(await _raw_sql(sql=sql, max_rows=normalized_max_rows))
    except ValueError as e:
        return _error(e, error_type="invalid_argument")
    except Exception as e:  # noqa: BLE001
        return _error(e, error_type="execution_failed")


# Streamable HTTP：stateless 多 worker 安全
mcp_http_app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)

# ★ 内层 ASGI transport：直接注册成 Route 而非 app.mount()。
# 实测 mount("/mcp", app) 会剥掉前缀让子应用收到空 path → 请求 /mcp/ 得 404；
# mount("/") 又会拦截所有 REST 请求。取出 transport 自己注册两条路由最干净
# （与参考实现 mcp_main.py 的做法一致）。
mcp_transport = mcp_http_app.routes[0].app
