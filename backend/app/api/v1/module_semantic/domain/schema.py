from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam, TenantByQueryParam, UserByQueryParam
from app.core.base_schema import BaseSchema, TenantBySchema, UserBySchema


class DomainCreateSchema(BaseModel):
    """创建业务域"""

    code: str = Field(..., min_length=1, max_length=50, description="业务域编码")
    name: str = Field(..., min_length=1, max_length=100, description="业务域名称")
    order: int = Field(default=0, ge=0, description="显示排序")
    description: str | None = Field(default=None, description="描述")

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("业务域编码不能为空")
        return v

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("业务域名称不能为空")
        return v


class DomainUpdateSchema(BaseModel):
    """更新业务域"""

    code: str | None = Field(default=None, max_length=50, description="业务域编码")
    name: str | None = Field(default=None, max_length=100, description="业务域名称")
    order: int | None = Field(default=None, ge=0, description="显示排序")
    description: str | None = Field(default=None, description="描述")
    status: int | None = Field(default=None, ge=0, le=1, description="状态(0:启用 1:停用)")


class DomainOutSchema(BaseSchema, UserBySchema, TenantBySchema):
    """业务域响应"""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="业务域编码")
    name: str = Field(..., description="业务域名称")
    order: int = Field(..., description="显示排序")
    description: str | None = Field(default=None, description="描述")
    status: int = Field(..., description="状态(0:启用 1:停用)")


class DomainBatchSchema(BaseModel):
    """批量更新状态"""

    ids: list[int] = Field(..., min_length=1, description="业务域ID列表")
    status: int = Field(..., ge=0, le=1, description="状态(0:启用 1:停用)")


@dataclass
class DomainQueryParam(BaseQueryParam, UserByQueryParam, TenantByQueryParam):
    """业务域查询参数"""

    code: str | None = None
    name: str | None = None
    status: int | None = Query(None, ge=0, le=1, description="状态")

    def __post_init__(self) -> None:
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
