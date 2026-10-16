"""同义词治理接口（参考方案 §6 闭环的第四环：人工审核入库）。

端点对应治理动作：
- `alias-candidates`：看挖掘出的候选（被反复问且被采纳的说法）
- `zero-recall`：看零召回清单（别名缺失最直接的证据，不需用户反馈就可用）
- `callers`：有哪些调用方（筛选框用；手打 caller 名打错会得到空列表，
  而空列表看起来像"没问过"不像"打错了"）
- `approve-alias`：逐条采纳，并进该资产的 aliases

★ 刻意没有"一键全部采纳"：人工审核的价值就在于逐条看过。给一个 approve_all
等于把这道闸拆了 —— 与 LLM 源守卫同一原则（自动产出物不得自动获得权威性）。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission
from app.core.exceptions import CustomException
from app.core.router_class import OperationLogRoute

from .schema import (  # noqa: F401
    AliasApproveResult,
    AliasApproveSchema,
    AliasCandidateOutSchema,
    CallerOutSchema,
    ZeroRecallOutSchema,
)
from .service import QueryLogService

QueryLogRouter = APIRouter(
    route_class=OperationLogRoute, prefix="/querylog", tags=["语义平台", "同义词治理"]
)


@QueryLogRouter.get(
    "/alias-candidates",
    summary="别名候选(挖掘待审)",
    response_model=ResponseSchema[list[AliasCandidateOutSchema]],
)
async def alias_candidates_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:querylog:list"]))],
    since_days: Annotated[int, Query(ge=1, le=365, description="回看天数")] = 30,
    min_hits: Annotated[int, Query(ge=1, le=100, description="最少被采纳次数")] = 2,
    limit: Annotated[int, Query(ge=1, le=200, description="返回条数")] = 50,
    caller: Annotated[str | None, Query(description="只看某调用方(如 codata);留空=全部")] = None,
) -> JSONResponse:
    """被反复问且**被明确采纳**的说法 → 该资产的候选别名。

    ★ 只统计 `adopted=true`。未反馈（NULL）既不算正例也不算负例 —— 绝大多数日志
    都是 NULL，把它当正例会把检索的错误答案固化成别名，让错误自我强化。
    """
    data = await QueryLogService(auth).mine_alias_candidates(
        since_days=since_days, min_hits=min_hits, limit=limit, caller=caller
    )
    return SuccessResponse(data=data)


@QueryLogRouter.get(
    "/callers",
    summary="调用方清单(筛选框用)",
    response_model=ResponseSchema[list[CallerOutSchema]],
)
async def callers_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:querylog:list"]))],
    since_days: Annotated[int, Query(ge=1, le=365, description="回看天数")] = 30,
) -> JSONResponse:
    """有哪些调用方问过东西，按提问量倒序。

    ★ 存在的理由：不然运营只能手打 caller 名，而打错一个字得到的是空列表 ——
    空列表看起来像"这个调用方没问过东西"，不像"你打错了"。归因错误比没功能更糟。
    """
    data = await QueryLogService(auth).list_callers(since_days=since_days)
    return SuccessResponse(data=data)


@QueryLogRouter.get(
    "/zero-recall",
    summary="零召回清单",
    response_model=ResponseSchema[list[ZeroRecallOutSchema]],
)
async def zero_recall_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:querylog:list"]))],
    since_days: Annotated[int, Query(ge=1, le=365, description="回看天数")] = 30,
    limit: Annotated[int, Query(ge=1, le=200, description="返回条数")] = 50,
    caller: Annotated[str | None, Query(description="只看某调用方;留空=全部")] = None,
) -> JSONResponse:
    """问了但一个候选都没搜到的说法。

    ★ 比采纳信号**更早可用**：不需要用户反馈，上线初期就能指导补别名。
    """
    data = await QueryLogService(auth).zero_recall_queries(
        since_days=since_days, limit=limit, caller=caller
    )
    return SuccessResponse(data=data)


@QueryLogRouter.post(
    "/approve-alias",
    summary="采纳别名候选",
    response_model=ResponseSchema[AliasApproveResult],
)
async def approve_alias_controller(
    data: Annotated[AliasApproveSchema, Body()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:querylog:approve"]))],
) -> JSONResponse:
    """把候选别名并进该资产的 aliases（合并，不覆盖运营手工补的）。

    写活表 + 同步检索副库，**立即生效**（不必等下次发布）。副库同步失败只告警，
    不回滚采纳 —— 活表是事实源，下次发布/rebuild 会补上。重复采纳幂等。
    """
    try:
        result = await QueryLogService(auth).approve_alias(
            alias=data.alias, node_type=data.node_type, node_code=data.node_code
        )
    except ValueError as e:
        raise CustomException(msg=str(e)) from e

    msg = "该别名此前已存在" if result["already_present"] else "采纳成功，已生效"
    return SuccessResponse(data=result, msg=msg)


@QueryLogRouter.post("/purge", summary="清理超期 query 日志", response_model=ResponseSchema)
async def purge_query_log_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:querylog:approve"]))],
    keep_days: Annotated[int, Query(ge=7, le=730, description="保留天数")] = 180,
) -> JSONResponse:
    """软删超期日志（默认留 180 天）。

    ★ 定时任务已每周自动清理一次（`semantic_querylog_purge`），本端点是**补救入口**：
    日志表已经涨大、现在就要清的场景。与 `retrieval/rebuild` 同时有定时与手动入口
    是同一逻辑。

    权限复用 `querylog:approve`（写动作），故无需新增权限点。
    """
    purged = await QueryLogService(auth).purge_old(keep_days=keep_days)
    return SuccessResponse(
        data={"purged": purged, "keep_days": keep_days},
        msg=f"已清理 {purged} 条超期日志（保留最近 {keep_days} 天）",
    )
