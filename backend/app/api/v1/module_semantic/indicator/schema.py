from dataclasses import dataclass
from datetime import datetime

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam, TenantByQueryParam, UserByQueryParam
from app.core.base_schema import BaseSchema, CommonSchema, TenantBySchema, UserBySchema

_INDICATOR_TYPES = {"atomic", "derived", "composite"}
_IMPL_ROLES = {"primary", "secondary"}


# ---------------------------------------------------------------------------
# 口径 Impl
# ---------------------------------------------------------------------------
class IndicatorImplCreateSchema(BaseModel):
    """创建/内嵌口径"""

    role: str = Field(default="secondary", description="口径角色(primary/secondary)")
    sql_text: str = Field(..., min_length=1, description="口径SQL")
    label: str | None = Field(default=None, max_length=200, description="口径标签")
    source_note: str | None = Field(default=None, description="溯源说明")
    data_layer: str | None = Field(default=None, max_length=10, description="数据层")
    granularity: str | None = Field(default=None, max_length=20, description="粒度")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in _IMPL_ROLES:
            raise ValueError("口径角色仅支持 primary(主) / secondary(副)")
        return v

    @field_validator("sql_text")
    @classmethod
    def _validate_sql(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("口径SQL不能为空")
        return v


class IndicatorImplOutSchema(BaseSchema, UserBySchema):
    """口径响应"""

    model_config = ConfigDict(from_attributes=True)

    indicator_id: int = Field(..., description="所属指标ID")
    role: str = Field(..., description="口径角色")
    sql_text: str = Field(..., description="口径SQL")
    label: str | None = Field(default=None, description="口径标签")
    source_note: str | None = Field(default=None, description="溯源说明")
    data_layer: str | None = Field(default=None, description="数据层")
    granularity: str | None = Field(default=None, description="粒度")
    review_status: int = Field(..., description="审核状态(0:待审 1:通过 2:驳回)")
    reviewed_id: int | None = Field(default=None, description="审核人ID")
    reviewed_time: datetime | None = Field(default=None, description="审核时间")


class IndicatorImplReviewSchema(BaseModel):
    """口径审核"""

    review_status: int = Field(..., ge=1, le=2, description="审核结果(1:通过 2:驳回)")


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
class IndicatorCreateSchema(BaseModel):
    """创建指标（含内嵌口径）"""

    code: str = Field(..., min_length=1, max_length=50, description="指标编码")
    name: str = Field(..., min_length=1, max_length=100, description="指标名称")
    aliases: str | None = Field(default=None, description="别名(JSON数组字符串)")
    description: str | None = Field(default=None, description="描述")
    business_definition: str | None = Field(default=None, description="业务定义")
    indicator_type: str = Field(default="atomic", description="指标类型(atomic/derived/composite)")
    domain: str | None = Field(default=None, max_length=100, description="业务域")
    business_process: str | None = Field(default=None, max_length=100, description="业务过程")
    measure_expression: str | None = Field(default=None, description="度量表达式")
    unit: str | None = Field(default=None, max_length=20, description="单位")
    data_type: str | None = Field(default=None, max_length=50, description="数据类型")
    additivity: str | None = Field(default=None, max_length=24, description="可加性")
    decimal_places: int = Field(default=2, ge=0, le=10, description="小数位数")
    biz_owner_id: int | None = Field(default=None, gt=0, description="业务负责人ID")
    tech_owner_id: int | None = Field(default=None, gt=0, description="技术负责人ID")
    business_importance: int | None = Field(default=None, ge=1, le=5, description="业务重要度(1-5)")
    primary_entity_id: int | None = Field(default=None, gt=0, description="主实体ID")
    available_dimension_ids: str | None = Field(default=None, description="可用维度ID列表(JSON数组字符串)")
    impls: list[IndicatorImplCreateSchema] = Field(default_factory=list, description="口径列表")

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("指标编码不能为空")
        return v

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("指标名称不能为空")
        return v

    @field_validator("indicator_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in _INDICATOR_TYPES:
            raise ValueError("指标类型仅支持 atomic / derived / composite")
        return v


class IndicatorUpdateSchema(BaseModel):
    """更新指标（口径为 None 时不改动口径；给出列表则整体替换）"""

    code: str | None = Field(default=None, max_length=50, description="指标编码")
    name: str | None = Field(default=None, max_length=100, description="指标名称")
    aliases: str | None = Field(default=None, description="别名(JSON数组字符串)")
    description: str | None = Field(default=None, description="描述")
    business_definition: str | None = Field(default=None, description="业务定义")
    indicator_type: str | None = Field(default=None, description="指标类型")
    domain: str | None = Field(default=None, max_length=100, description="业务域")
    business_process: str | None = Field(default=None, max_length=100, description="业务过程")
    measure_expression: str | None = Field(default=None, description="度量表达式")
    unit: str | None = Field(default=None, max_length=20, description="单位")
    data_type: str | None = Field(default=None, max_length=50, description="数据类型")
    additivity: str | None = Field(default=None, max_length=24, description="可加性")
    decimal_places: int | None = Field(default=None, ge=0, le=10, description="小数位数")
    biz_owner_id: int | None = Field(default=None, gt=0, description="业务负责人ID")
    tech_owner_id: int | None = Field(default=None, gt=0, description="技术负责人ID")
    business_importance: int | None = Field(default=None, ge=1, le=5, description="业务重要度(1-5)")
    primary_entity_id: int | None = Field(default=None, gt=0, description="主实体ID")
    available_dimension_ids: str | None = Field(default=None, description="可用维度ID列表(JSON数组字符串)")
    impls: list[IndicatorImplCreateSchema] | None = Field(default=None, description="口径列表(给出则整体替换)")

    @field_validator("indicator_type")
    @classmethod
    def _validate_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _INDICATOR_TYPES:
            raise ValueError("指标类型仅支持 atomic / derived / composite")
        return v


class IndicatorOutSchema(BaseSchema, UserBySchema, TenantBySchema):
    """指标响应"""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="指标编码")
    name: str = Field(..., description="指标名称")
    aliases: str | None = Field(default=None, description="别名")
    description: str | None = Field(default=None, description="描述")
    business_definition: str | None = Field(default=None, description="业务定义")
    indicator_type: str = Field(..., description="指标类型")
    domain: str | None = Field(default=None, description="业务域")
    business_process: str | None = Field(default=None, description="业务过程")
    measure_expression: str | None = Field(default=None, description="度量表达式")
    unit: str | None = Field(default=None, description="单位")
    data_type: str | None = Field(default=None, description="数据类型")
    additivity: str | None = Field(default=None, description="可加性")
    decimal_places: int = Field(..., description="小数位数")
    biz_owner_id: int | None = Field(default=None, description="业务负责人ID")
    biz_owner: CommonSchema | None = Field(default=None, description="业务负责人")
    tech_owner_id: int | None = Field(default=None, description="技术负责人ID")
    tech_owner: CommonSchema | None = Field(default=None, description="技术负责人")
    business_importance: int | None = Field(default=None, description="业务重要度")
    primary_entity_id: int | None = Field(default=None, description="主实体ID")
    available_dimension_ids: str | None = Field(default=None, description="可用维度ID列表")
    lifecycle_status: int = Field(..., description="生命周期状态(0:草稿 1:待审 2:已发布 3:已归档)")
    impls: list[IndicatorImplOutSchema] = Field(default_factory=list, description="口径列表")


class IndicatorStatusSchema(BaseModel):
    """指标状态流转"""

    lifecycle_status: int = Field(..., ge=0, le=3, description="目标状态(0:草稿 1:待审 2:已发布 3:已归档)")


@dataclass
class IndicatorQueryParam(BaseQueryParam, UserByQueryParam, TenantByQueryParam):
    """指标查询参数"""

    code: str | None = None
    name: str | None = None
    indicator_type: str | None = None
    domain: str | None = None
    lifecycle_status: int | None = Query(None, ge=0, le=3, description="生命周期状态")

    def __post_init__(self) -> None:
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
        if self.indicator_type:
            self.indicator_type = (QueueEnum.eq.value, self.indicator_type)
        if self.domain:
            self.domain = (QueueEnum.like.value, self.domain)
        if isinstance(self.lifecycle_status, int):
            self.lifecycle_status = (QueueEnum.eq.value, self.lifecycle_status)
