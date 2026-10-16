from dataclasses import dataclass
from datetime import date, datetime

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam, TenantByQueryParam, UserByQueryParam
from app.core.base_schema import BaseSchema, TenantBySchema, UserBySchema

_KNOWLEDGE_TYPES = {"background", "usage", "caveat", "query_pattern", "glossary"}
_USAGE_MODES = {"always", "auto", "never"}
_TARGET_TYPES = {"entity", "field", "dimension", "indicator", "knowledge", "join"}
_REF_TYPES = {"describes", "howto", "caveats", "contradicts"}


# ---------------------------------------------------------------------------
# 交叉引用
# ---------------------------------------------------------------------------
class KnowledgeRefCreateSchema(BaseModel):
    """创建/内嵌交叉引用"""

    target_type: str = Field(..., description="目标类型")
    target_id: int = Field(..., gt=0, description="目标实体ID")
    target_label: str | None = Field(default=None, max_length=200, description="目标显示名")
    ref_type: str = Field(default="describes", description="引用类型")

    @field_validator("target_type")
    @classmethod
    def _vt(cls, v: str) -> str:
        if v not in _TARGET_TYPES:
            raise ValueError("目标类型仅支持 entity/field/dimension/indicator/knowledge/join")
        return v

    @field_validator("ref_type")
    @classmethod
    def _vr(cls, v: str) -> str:
        if v not in _REF_TYPES:
            raise ValueError("引用类型仅支持 describes/howto/caveats/contradicts")
        return v


class KnowledgeRefOutSchema(BaseSchema):
    """引用响应"""

    model_config = ConfigDict(from_attributes=True)

    knowledge_id: int = Field(..., description="所属知识ID")
    target_type: str = Field(..., description="目标类型")
    target_id: int = Field(..., description="目标ID")
    target_label: str | None = Field(default=None, description="目标显示名")
    ref_type: str = Field(..., description="引用类型")


# ---------------------------------------------------------------------------
# 知识条目
# ---------------------------------------------------------------------------
class KnowledgeCreateSchema(BaseModel):
    """创建知识（含内嵌引用）"""

    key: str = Field(..., min_length=1, max_length=100, description="知识键")
    summary: str = Field(..., min_length=1, max_length=200, description="摘要")
    body: str | None = Field(default=None, description="正文(Markdown)")
    knowledge_type: str = Field(default="background", description="类型")
    representative_sql: str | None = Field(default=None, description="示例SQL")
    tags: str | None = Field(default=None, description="标签(JSON数组字符串)")
    usage_mode: str = Field(default="auto", description="使用模式(always/auto/never)")
    source: str = Field(default="manual", description="来源")
    confidence: int | None = Field(default=None, ge=1, le=5, description="置信度(1-5)")
    owner_id: int | None = Field(default=None, gt=0, description="知识负责人ID")
    reviewed_at: datetime | None = Field(default=None, description="最近复核时间")
    review_interval_days: int = Field(default=180, ge=1, description="复核周期(天)")
    caliber_break_date: date | None = Field(
        default=None,
        description="口径分水岭：填**最后一个旧口径日**，新口径从次日起生效。留空则不启用该护栏",
    )
    caliber_break_note: str | None = Field(
        default=None, max_length=200, description="口径差异一句话说明(会进拒答消息给 agent)"
    )
    refs: list[KnowledgeRefCreateSchema] = Field(default_factory=list, description="交叉引用列表")

    @field_validator("key")
    @classmethod
    def _vk(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("知识键不能为空")
        return v

    @field_validator("knowledge_type")
    @classmethod
    def _vkt(cls, v: str) -> str:
        if v not in _KNOWLEDGE_TYPES:
            raise ValueError("类型仅支持 background/usage/caveat/query_pattern/glossary")
        return v

    @field_validator("usage_mode")
    @classmethod
    def _vum(cls, v: str) -> str:
        if v not in _USAGE_MODES:
            raise ValueError("使用模式仅支持 always/auto/never")
        return v


class KnowledgeUpdateSchema(BaseModel):
    """更新知识（refs 为 None 不动；给出则整体替换）"""

    key: str | None = Field(default=None, max_length=100, description="知识键")
    summary: str | None = Field(default=None, max_length=200, description="摘要")
    body: str | None = Field(default=None, description="正文")
    knowledge_type: str | None = Field(default=None, description="类型")
    representative_sql: str | None = Field(default=None, description="示例SQL")
    tags: str | None = Field(default=None, description="标签")
    usage_mode: str | None = Field(default=None, description="使用模式")
    source: str | None = Field(default=None, description="来源")
    confidence: int | None = Field(default=None, ge=1, le=5, description="置信度")
    owner_id: int | None = Field(default=None, gt=0, description="知识负责人ID")
    reviewed_at: datetime | None = Field(default=None, description="最近复核时间")
    review_interval_days: int | None = Field(default=None, ge=1, description="复核周期(天)")
    needs_review: bool | None = Field(default=None, description="待复核")
    caliber_break_date: date | None = Field(default=None, description="口径分水岭(最后一个旧口径日)")
    caliber_break_note: str | None = Field(default=None, max_length=200, description="口径差异说明")
    refs: list[KnowledgeRefCreateSchema] | None = Field(default=None, description="引用列表(给出则整体替换)")

    @field_validator("knowledge_type")
    @classmethod
    def _vkt(cls, v: str | None) -> str | None:
        if v is not None and v not in _KNOWLEDGE_TYPES:
            raise ValueError("类型仅支持 background/usage/caveat/query_pattern/glossary")
        return v

    @field_validator("usage_mode")
    @classmethod
    def _vum(cls, v: str | None) -> str | None:
        if v is not None and v not in _USAGE_MODES:
            raise ValueError("使用模式仅支持 always/auto/never")
        return v


class KnowledgeOutSchema(BaseSchema, UserBySchema, TenantBySchema):
    """知识响应"""

    model_config = ConfigDict(from_attributes=True)

    key: str = Field(..., description="知识键")
    summary: str = Field(..., description="摘要")
    body: str | None = Field(default=None, description="正文")
    knowledge_type: str = Field(..., description="类型")
    representative_sql: str | None = Field(default=None, description="示例SQL")
    tags: str | None = Field(default=None, description="标签")
    usage_mode: str = Field(..., description="使用模式")
    source: str = Field(..., description="来源")
    confidence: int | None = Field(default=None, description="置信度")
    status: int = Field(..., description="状态")
    owner_id: int | None = Field(default=None, description="知识负责人ID")
    reviewed_at: datetime | None = Field(default=None, description="最近复核时间")
    review_interval_days: int = Field(default=180, description="复核周期(天)")
    needs_review: bool = Field(default=False, description="待复核")
    caliber_break_date: date | None = Field(default=None, description="口径分水岭(最后一个旧口径日)")
    caliber_break_note: str | None = Field(default=None, description="口径差异说明")
    refs: list[KnowledgeRefOutSchema] = Field(default_factory=list, description="交叉引用列表")


class KnowledgeStatusSchema(BaseModel):
    """知识状态流转"""

    status: int = Field(..., ge=0, le=3, description="目标状态")


@dataclass
class KnowledgeQueryParam(BaseQueryParam, UserByQueryParam, TenantByQueryParam):
    """知识查询参数"""

    key: str | None = None
    summary: str | None = None
    knowledge_type: str | None = None
    usage_mode: str | None = None
    status: int | None = Query(None, ge=0, le=3, description="状态")

    def __post_init__(self) -> None:
        if self.key:
            self.key = (QueueEnum.like.value, self.key)
        if self.summary:
            self.summary = (QueueEnum.like.value, self.summary)
        if self.knowledge_type:
            self.knowledge_type = (QueueEnum.eq.value, self.knowledge_type)
        if self.usage_mode:
            self.usage_mode = (QueueEnum.eq.value, self.usage_mode)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
