from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam, TenantByQueryParam, UserByQueryParam
from app.core.base_schema import BaseSchema, CommonSchema, TenantBySchema, UserBySchema

_ENTITY_TYPES = {"fact", "dimension", "detail", "summary"}
_FIELD_ROLES = {"measure", "dimension", "time", "id"}
_VISIBILITY = {"public", "internal", "hidden"}


# ---------------------------------------------------------------------------
# 字段 Field
# ---------------------------------------------------------------------------
class FieldCreateSchema(BaseModel):
    """创建/内嵌业务字段"""

    code: str = Field(..., min_length=1, max_length=50, description="字段编码")
    name: str = Field(..., min_length=1, max_length=100, description="字段业务名")
    physical_column_id: int | None = Field(default=None, gt=0, description="挂钩物理列ID(L1)")
    physical_column: str | None = Field(default=None, max_length=100, description="对应物理列名")
    expr: str | None = Field(default=None, description="计算表达式")
    field_role: str = Field(default="dimension", description="字段角色(measure/dimension/time/id)")
    data_type: str | None = Field(default=None, max_length=20, description="语义类型")
    unit: str | None = Field(default=None, max_length=20, description="单位")
    enum_values: str | None = Field(default=None, description="枚举(JSON数组字符串)")
    visibility: str = Field(default="public", description="可见性(public/internal/hidden)")
    description: str | None = Field(default=None, description="业务含义")

    @field_validator("field_role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in _FIELD_ROLES:
            raise ValueError("字段角色仅支持 measure/dimension/time/id")
        return v

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: str) -> str:
        if v not in _VISIBILITY:
            raise ValueError("可见性仅支持 public/internal/hidden")
        return v


class FieldOutSchema(BaseSchema, UserBySchema):
    """字段响应"""

    model_config = ConfigDict(from_attributes=True)

    entity_id: int = Field(..., description="所属实体ID")
    code: str = Field(..., description="字段编码")
    name: str = Field(..., description="字段业务名")
    physical_column_id: int | None = Field(default=None, description="挂钩物理列ID(L1)")
    physical_column: str | None = Field(default=None, description="物理列名")
    expr: str | None = Field(default=None, description="计算表达式")
    field_role: str = Field(..., description="字段角色")
    data_type: str | None = Field(default=None, description="语义类型")
    unit: str | None = Field(default=None, description="单位")
    enum_values: str | None = Field(default=None, description="枚举")
    visibility: str = Field(..., description="可见性")
    description: str | None = Field(default=None, description="业务含义")


# ---------------------------------------------------------------------------
# 实体 Entity
# ---------------------------------------------------------------------------
class EntityCreateSchema(BaseModel):
    """创建业务实体（含内嵌字段）"""

    code: str = Field(..., min_length=1, max_length=50, description="实体编码")
    name: str = Field(..., min_length=1, max_length=100, description="实体名称")
    entity_type: str = Field(default="fact", description="实体类型(fact/dimension/detail/summary)")
    description: str | None = Field(default=None, description="业务描述")
    domain_code: str | None = Field(default=None, max_length=50, description="业务域编码")
    physical_table_id: int | None = Field(default=None, gt=0, description="挂钩物理表ID(L1)")
    physical_datasource: str | None = Field(default=None, max_length=100, description="物理数据源")
    physical_database: str | None = Field(default=None, max_length=100, description="物理库名")
    physical_table: str | None = Field(default=None, max_length=200, description="物理表名")
    grain: str | None = Field(default=None, max_length=200, description="粒度")
    default_time_field: str | None = Field(default=None, max_length=100, description="默认时间字段")
    owner_id: int | None = Field(default=None, gt=0, description="负责人ID")
    fields: list[FieldCreateSchema] = Field(default_factory=list, description="字段列表")

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("实体编码不能为空")
        return v

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("实体名称不能为空")
        return v

    @field_validator("entity_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in _ENTITY_TYPES:
            raise ValueError("实体类型仅支持 fact/dimension/detail/summary")
        return v


class EntityUpdateSchema(BaseModel):
    """更新业务实体（fields 为 None 时不动字段；给出列表则整体替换）"""

    code: str | None = Field(default=None, max_length=50, description="实体编码")
    name: str | None = Field(default=None, max_length=100, description="实体名称")
    entity_type: str | None = Field(default=None, description="实体类型")
    description: str | None = Field(default=None, description="业务描述")
    domain_code: str | None = Field(default=None, max_length=50, description="业务域编码")
    physical_table_id: int | None = Field(default=None, gt=0, description="挂钩物理表ID(L1)")
    physical_datasource: str | None = Field(default=None, max_length=100, description="物理数据源")
    physical_database: str | None = Field(default=None, max_length=100, description="物理库名")
    physical_table: str | None = Field(default=None, max_length=200, description="物理表名")
    grain: str | None = Field(default=None, max_length=200, description="粒度")
    default_time_field: str | None = Field(default=None, max_length=100, description="默认时间字段")
    owner_id: int | None = Field(default=None, gt=0, description="负责人ID")
    fields: list[FieldCreateSchema] | None = Field(default=None, description="字段列表(给出则整体替换)")

    @field_validator("entity_type")
    @classmethod
    def _validate_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _ENTITY_TYPES:
            raise ValueError("实体类型仅支持 fact/dimension/detail/summary")
        return v


class EntityOutSchema(BaseSchema, UserBySchema, TenantBySchema):
    """实体响应"""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="实体编码")
    name: str = Field(..., description="实体名称")
    entity_type: str = Field(..., description="实体类型")
    description: str | None = Field(default=None, description="业务描述")
    domain_code: str | None = Field(default=None, description="业务域编码")
    physical_table_id: int | None = Field(default=None, description="挂钩物理表ID(L1)")
    physical_datasource: str | None = Field(default=None, description="物理数据源")
    physical_database: str | None = Field(default=None, description="物理库名")
    physical_table: str | None = Field(default=None, description="物理表名")
    grain: str | None = Field(default=None, description="粒度")
    default_time_field: str | None = Field(default=None, description="默认时间字段")
    owner_id: int | None = Field(default=None, description="负责人ID")
    owner: CommonSchema | None = Field(default=None, description="负责人")
    status: int = Field(..., description="状态(0:草稿 1:待审 2:已发布 3:已归档)")
    fields: list[FieldOutSchema] = Field(default_factory=list, description="字段列表")


class EntityStatusSchema(BaseModel):
    """实体状态流转"""

    status: int = Field(..., ge=0, le=3, description="目标状态(0:草稿 1:待审 2:已发布 3:已归档)")


@dataclass
class EntityQueryParam(BaseQueryParam, UserByQueryParam, TenantByQueryParam):
    """实体查询参数"""

    code: str | None = None
    name: str | None = None
    entity_type: str | None = None
    domain_code: str | None = None
    status: int | None = Query(None, ge=0, le=3, description="状态")

    def __post_init__(self) -> None:
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
        if self.entity_type:
            self.entity_type = (QueueEnum.eq.value, self.entity_type)
        if self.domain_code:
            self.domain_code = (QueueEnum.eq.value, self.domain_code)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
