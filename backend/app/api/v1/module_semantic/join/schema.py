from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam, TenantByQueryParam, UserByQueryParam
from app.core.base_schema import BaseSchema, CommonSchema, TenantBySchema, UserBySchema

_RELATIONSHIPS = {"many_to_one", "one_to_many", "one_to_one"}
_JOIN_TYPES = {"inner", "left"}


class JoinCreateSchema(BaseModel):
    """创建 join 关系"""

    from_entity_id: int = Field(..., gt=0, description="左实体ID")
    to_entity_id: int = Field(..., gt=0, description="右实体ID")
    on_expr: str = Field(..., min_length=1, max_length=500, description="join 条件")
    relationship_type: str = Field(default="many_to_one", description="关系类型")
    join_type: str = Field(default="left", description="join 类型")
    description: str | None = Field(default=None, description="说明")

    @field_validator("relationship_type")
    @classmethod
    def _validate_rel(cls, v: str) -> str:
        if v not in _RELATIONSHIPS:
            raise ValueError("关系类型仅支持 many_to_one/one_to_many/one_to_one")
        return v

    @field_validator("join_type")
    @classmethod
    def _validate_jt(cls, v: str) -> str:
        if v not in _JOIN_TYPES:
            raise ValueError("join 类型仅支持 inner/left")
        return v


class JoinUpdateSchema(BaseModel):
    """更新 join 关系"""

    from_entity_id: int | None = Field(default=None, gt=0, description="左实体ID")
    to_entity_id: int | None = Field(default=None, gt=0, description="右实体ID")
    on_expr: str | None = Field(default=None, max_length=500, description="join 条件")
    relationship_type: str | None = Field(default=None, description="关系类型")
    join_type: str | None = Field(default=None, description="join 类型")
    description: str | None = Field(default=None, description="说明")


class JoinOutSchema(BaseSchema, UserBySchema, TenantBySchema):
    """join 响应"""

    model_config = ConfigDict(from_attributes=True)

    from_entity_id: int = Field(..., description="左实体ID")
    from_entity: CommonSchema | None = Field(default=None, description="左实体")
    to_entity_id: int = Field(..., description="右实体ID")
    to_entity: CommonSchema | None = Field(default=None, description="右实体")
    on_expr: str = Field(..., description="join 条件")
    relationship_type: str = Field(..., description="关系类型")
    join_type: str = Field(..., description="join 类型")
    description: str | None = Field(default=None, description="说明")
    status: int = Field(..., description="状态")


class JoinStatusSchema(BaseModel):
    """join 状态流转"""

    status: int = Field(..., ge=0, le=3, description="目标状态")


class GraphNodeSchema(BaseModel):
    """关系图节点"""

    id: int = Field(..., description="实体ID")
    code: str = Field(..., description="实体编码")
    name: str = Field(..., description="实体名称")
    entity_type: str = Field(..., description="实体类型")


class GraphEdgeSchema(BaseModel):
    """关系图边"""

    id: int = Field(..., description="join ID")
    from_entity_id: int = Field(..., description="左实体ID")
    to_entity_id: int = Field(..., description="右实体ID")
    relationship_type: str = Field(..., description="关系类型")
    on_expr: str = Field(..., description="join 条件")


class GraphSchema(BaseModel):
    """join 关系图"""

    nodes: list[GraphNodeSchema] = Field(default_factory=list, description="节点(实体)")
    edges: list[GraphEdgeSchema] = Field(default_factory=list, description="边(join)")


@dataclass
class JoinQueryParam(BaseQueryParam, UserByQueryParam, TenantByQueryParam):
    """join 查询参数"""

    from_entity_id: int | None = None
    to_entity_id: int | None = None
    status: int | None = Query(None, ge=0, le=3, description="状态")

    def __post_init__(self) -> None:
        if self.from_entity_id:
            self.from_entity_id = (QueueEnum.eq.value, self.from_entity_id)
        if self.to_entity_id:
            self.to_entity_id = (QueueEnum.eq.value, self.to_entity_id)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
