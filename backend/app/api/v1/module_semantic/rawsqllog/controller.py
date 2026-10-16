"""裸 SQL 调用审计接口。

## 权限点为什么独立于 querylog

审计含 **SQL 原文**，能看它的人等于能看到查询意图与表结构。并进 `querylog:list`
等于给"看同义词候选"的人顺带发了"看别人查了什么" —— 与 `model:grant` 和
`model:update` 分开是同一原则（授权不该由相邻权限顺带获得）。

## 刻意不做

- **导出**：要走文件下载与鉴权，独立议题
- **"重跑这条 SQL"按钮**：那等于给审计页一个执行入口，把只读的观测面变成操作面
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import RawSqlLogOutSchema  # noqa: F401 - response_model 用
from .service import _DEFAULT_RETENTION_DAYS, RawSqlLogService

RawSqlLogRouter = APIRouter(
    route_class=OperationLogRoute, prefix="/rawsqllog", tags=["语义平台", "裸 SQL 审计"]
)


@RawSqlLogRouter.get(
    "/recent",
    summary="最近的裸 SQL 调用",
    response_model=ResponseSchema[list[RawSqlLogOutSchema]],
)
async def recent_raw_sql_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:rawsqllog:list"]))],
    since_days: Annotated[int, Query(ge=1, le=365, description="回看天数")] = 7,
    caller: Annotated[str | None, Query(description="只看某调用方;留空=全部")] = None,
    only_failed: Annotated[bool, Query(description="只看失败的(排查滥用最常用)")] = False,
    limit: Annotated[int, Query(ge=1, le=500, description="返回条数")] = 100,
) -> JSONResponse:
    """最近的 `execute_sql` 调用，倒序。

    ★ 这些 SQL **未经口径审核** —— 看到的数字不代表权威口径。
    """
    rows = await RawSqlLogService(auth).recent(
        since_days=since_days, caller=caller, only_failed=only_failed, limit=limit,
    )
    return SuccessResponse(data=rows)


@RawSqlLogRouter.post("/purge", summary="清理超期审计", response_model=ResponseSchema)
async def purge_raw_sql_log_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_semantic:rawsqllog:purge"]))],
    keep_days: Annotated[int, Query(ge=7, le=730, description="保留天数")] = _DEFAULT_RETENTION_DAYS,
) -> JSONResponse:
    """软删超期审计（默认留 90 天）。

    定时任务已随 `semantic_querylog_purge` 每周清一次，本端点是**补救入口**：
    审计表已经涨大、现在就要清的场景。
    """
    purged = await RawSqlLogService(auth).purge_old(keep_days=keep_days)
    return SuccessResponse(
        data={"purged": purged, "keep_days": keep_days},
        msg=f"已清理 {purged} 条超期审计（保留最近 {keep_days} 天）",
    )
