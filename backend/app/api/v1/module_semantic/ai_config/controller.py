"""嵌入配置接口（系统设置 → AI 配置）。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .schema import AiConfigOutSchema, AiConfigSaveResult, AiConfigUpdateSchema  # noqa: F401
from .service import AiConfigService

AiConfigRouter = APIRouter(
    route_class=OperationLogRoute, prefix="/aiconfig", tags=["系统管理", "AI 配置"]
)


@AiConfigRouter.get("/detail", summary="嵌入配置详情", response_model=ResponseSchema[AiConfigOutSchema])
async def ai_config_detail_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_system:aiconfig:detail"]))],
) -> JSONResponse:
    """读嵌入模型配置。★ api_key 只返回掩码，不返回明文。"""
    return SuccessResponse(data=await AiConfigService(auth).get())


@AiConfigRouter.put("/update", summary="保存嵌入配置", response_model=ResponseSchema[AiConfigSaveResult])
async def ai_config_update_controller(
    data: Annotated[AiConfigUpdateSchema, Body()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_system:aiconfig:update"]))],
) -> JSONResponse:
    """保存配置。api_key 留空表示不修改。

    ★ 维度变更会自动重建 pgvector 列并清空旧向量（返回 dimension_changed 与 cleared），
    前端据此提示"向量索引重建中"。
    """
    result = await AiConfigService(auth).save(data)
    msg = (
        f"保存成功；维度由 {result.old_dimensions} 变为 {result.new_dimensions}，"
        f"已清空 {result.cleared} 条旧向量，将自动重新计算"
        if result.dimension_changed else "保存成功"
    )
    return SuccessResponse(data=result, msg=msg)


@AiConfigRouter.post("/test", summary="测试嵌入端点", response_model=ResponseSchema)
async def ai_config_test_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_system:aiconfig:detail"]))],
    data: Annotated[AiConfigUpdateSchema | None, Body()] = None,
) -> JSONResponse:
    """试调端点并核对维度是否与配置一致（维度不符是最常见的配错）。

    ★ 带 body 时测**表单里的当前值**（不必先保存）；不带 body 则测已保存的配置。
    表单 api_key 留空时沿用已存的密钥。
    """
    result = await AiConfigService(auth).test_connection(override=data)
    return SuccessResponse(data=result, msg="连接正常" if result.get("ok") else "连接失败")


@AiConfigRouter.post("/rebuild-vectors", summary="重建向量索引", response_model=ResponseSchema)
async def ai_config_rebuild_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_system:aiconfig:update"]))],
) -> JSONResponse:
    """手工重建向量列并清空旧向量（维度变更时会自动做，此处供补救）。"""
    service = AiConfigService(auth)
    cfg = await service.resolve()
    return SuccessResponse(data=await service.rebuild_vectors(cfg.dimensions))
