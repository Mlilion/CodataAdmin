from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam, TenantByQueryParam, UserByQueryParam
from app.core.base_schema import BaseSchema, TenantBySchema, UserBySchema


class DimensionCreateSchema(BaseModel):
    """创建维度"""

    code: str = Field(..., min_length=1, max_length=50, description="维度编码")
    name: str = Field(..., min_length=1, max_length=100, description="维度名称")
    dimension_type: str | None = Field(default=None, max_length=20, description="维度类型")
    description: str | None = Field(default=None, description="描述")
    aliases: str | None = Field(default=None, description="别名(JSON数组字符串)")
    bind_entity_id: int | None = Field(default=None, gt=0, description="绑定实体ID")
    bind_field_code: str | None = Field(default=None, max_length=50, description="绑定字段编码")

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("维度编码不能为空")
        return v

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("维度名称不能为空")
        return v


class DimensionUpdateSchema(BaseModel):
    """更新维度"""

    code: str | None = Field(default=None, max_length=50, description="维度编码")
    name: str | None = Field(default=None, max_length=100, description="维度名称")
    dimension_type: str | None = Field(default=None, max_length=20, description="维度类型")
    description: str | None = Field(default=None, description="描述")
    aliases: str | None = Field(default=None, description="别名(JSON数组字符串)")
    bind_entity_id: int | None = Field(default=None, gt=0, description="绑定实体ID")
    bind_field_code: str | None = Field(default=None, max_length=50, description="绑定字段编码")


class DimensionOutSchema(BaseSchema, UserBySchema, TenantBySchema):
    """维度响应"""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="维度编码")
    name: str = Field(..., description="维度名称")
    dimension_type: str | None = Field(default=None, description="维度类型")
    description: str | None = Field(default=None, description="描述")
    aliases: str | None = Field(default=None, description="别名(JSON数组字符串)")
    bind_entity_id: int | None = Field(default=None, description="绑定实体ID")
    bind_field_code: str | None = Field(default=None, description="绑定字段编码")
    status: int = Field(..., description="状态(0:草稿 1:待审 2:已发布 3:已归档)")


class DimensionStatusSchema(BaseModel):
    """维度状态流转"""

    status: int = Field(..., ge=0, le=3, description="目标状态(0:草稿 1:待审 2:已发布 3:已归档)")


@dataclass
class DimensionQueryParam(BaseQueryParam, UserByQueryParam, TenantByQueryParam):
    """维度查询参数"""

    code: str | None = None
    name: str | None = None
    dimension_type: str | None = None
    status: int | None = Query(None, ge=0, le=3, description="状态")

    def __post_init__(self) -> None:
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
        if self.dimension_type:
            self.dimension_type = (QueueEnum.eq.value, self.dimension_type)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
