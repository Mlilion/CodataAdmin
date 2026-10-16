from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam
from app.core.base_schema import BaseSchema

# 可见性取值。与 retrieval/visibility.py 的判据一一对应
_VISIBILITY_CHOICES = {"public", "restricted"}


class ModelCreateSchema(BaseModel):
    """创建语义模型"""

    code: str = Field(..., min_length=1, max_length=50, description="模型编码(域内唯一)")
    name: str = Field(..., min_length=1, max_length=100, description="模型名称")
    domain_id: int | None = Field(default=None, gt=0, description="归属业务域ID")
    description: str | None = Field(default=None, description="分析场景说明")
    default_time_grain: str | None = Field(default=None, max_length=20, description="默认时间粒度(day/week/month)")
    owner_id: int | None = Field(default=None, gt=0, description="模型负责人ID")
    # ★ 默认 public 不是随手写的: 若默认 restricted, 新建模型对所有 MCP token 立刻
    # 不可见(授权表是空的), 是可用性倒退。隔离必须是显式启用的能力。
    visibility: str = Field(
        default="public", description="可见性(public:所有调用方 / restricted:仅授权用户)"
    )

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: str) -> str:
        # 白名单而非自由文本: 拼错一个字母(如 "restrict")会静默退化成"所有人可见" ——
        # 访问控制字段的错误必须在写入时炸掉, 不能等到泄露之后才发现
        if v not in _VISIBILITY_CHOICES:
            raise ValueError(f"可见性只能是 {' / '.join(sorted(_VISIBILITY_CHOICES))}")
        return v

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("模型编码不能为空")
        return v

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("模型名称不能为空")
        return v


class ModelUpdateSchema(BaseModel):
    """更新语义模型"""

    code: str | None = Field(default=None, max_length=50, description="模型编码")
    name: str | None = Field(default=None, max_length=100, description="模型名称")
    domain_id: int | None = Field(default=None, gt=0, description="归属业务域ID")
    description: str | None = Field(default=None, description="分析场景说明")
    default_time_grain: str | None = Field(default=None, max_length=20, description="默认时间粒度")
    owner_id: int | None = Field(default=None, gt=0, description="模型负责人ID")
    status: int | None = Field(default=None, ge=0, le=3, description="状态(0:草稿 1:待审 2:已发布 3:已归档)")
    visibility: str | None = Field(default=None, description="可见性(public / restricted)")

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: str | None) -> str | None:
        # 同 create: 拼错会静默退化成"所有人可见", 必须在写入时炸掉
        if v is not None and v not in _VISIBILITY_CHOICES:
            raise ValueError(f"可见性只能是 {' / '.join(sorted(_VISIBILITY_CHOICES))}")
        return v


class ModelOutSchema(BaseSchema):
    """语义模型响应"""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="模型编码")
    name: str = Field(..., description="模型名称")
    domain_id: int | None = Field(default=None, description="归属业务域ID")
    description: str | None = Field(default=None, description="分析场景说明")
    default_time_grain: str | None = Field(default=None, description="默认时间粒度")
    version: int = Field(..., description="发布版本号")
    status: int = Field(..., description="状态(0:草稿 1:待审 2:已发布 3:已归档)")
    owner_id: int | None = Field(default=None, description="模型负责人ID")
    # 出参必须带: 否则前端画不出"这个模型是受限的", 授权面板也无从判断该不该出现
    visibility: str = Field(default="public", description="可见性(public / restricted)")


# ---- 用户授权（MCP 用户隔离的管理面）----


class GrantAddSchema(BaseModel):
    """授权给一批用户"""

    user_ids: list[int] = Field(..., min_length=1, description="被授权用户ID列表")


class GrantOutSchema(BaseSchema):
    """授权记录响应（带用户名，否则页面上只有一串 id）"""

    model_config = ConfigDict(from_attributes=True)

    model_id: int = Field(..., description="模型ID")
    user_id: int = Field(..., description="被授权用户ID")
    username: str | None = Field(default=None, description="用户名")
    user_name: str | None = Field(default=None, description="用户姓名")


# ---- 成员（模型↔实体）----


class MemberAddSchema(BaseModel):
    """添加模型成员实体"""

    entity_id: int = Field(..., gt=0, description="实体ID")
    role: str = Field(default="related", max_length=20, description="角色(primary主事实/related关联)")
    sort: int = Field(default=0, ge=0, description="展示顺序")


class MemberUpdateSchema(BaseModel):
    """更新成员(角色/排序)"""

    role: str | None = Field(default=None, max_length=20, description="角色(primary/related)")
    sort: int | None = Field(default=None, ge=0, description="展示顺序")


class MemberOutSchema(BaseSchema):
    """模型成员响应(含实体摘要)"""

    model_config = ConfigDict(from_attributes=True)

    model_id: int = Field(..., description="模型ID")
    entity_id: int = Field(..., description="实体ID")
    role: str = Field(..., description="角色")
    sort: int = Field(..., description="展示顺序")
    entity_code: str | None = Field(default=None, description="实体编码")
    entity_name: str | None = Field(default=None, description="实体名称")
    entity_type: str | None = Field(default=None, description="实体类型")
    entity_status: int | None = Field(default=None, description="实体状态")


# ---- 树（模型树主页一次性组装）----


class CompletenessSchema(BaseModel):
    """节点完整度(设计§四: 树上的灯)"""

    light: str = Field(..., description="green/yellow/red")
    missing: list[str] = Field(default=[], description="缺失项清单")


class TreeKnowledgeSchema(BaseModel):
    """树上挂载的知识条目"""

    id: int
    key: str
    summary: str
    knowledge_type: str
    usage_mode: str
    ref_type: str
    status: int
    completeness: CompletenessSchema | None = None


class TreeFieldSchema(BaseModel):
    """实体下的业务字段节点(sem_field 无状态机)"""

    id: int
    code: str
    name: str
    field_role: str | None = None
    data_type: str | None = None
    physical_column_id: int | None = None
    physical_column: str | None = None
    expr: str | None = None
    completeness: CompletenessSchema | None = None


class TreeDimensionSchema(BaseModel):
    """实体下归组的维度节点"""

    id: int
    code: str
    name: str
    dimension_type: str | None = None
    bind_field_code: str | None = None
    status: int
    completeness: CompletenessSchema | None = None


class TreeIndicatorSchema(BaseModel):
    """实体下归组的指标节点"""

    id: int
    code: str
    name: str
    indicator_type: str
    status: int
    completeness: CompletenessSchema | None = None


class TreeJoinSchema(BaseModel):
    """模型成员实体间的 JOIN 边"""

    id: int
    from_entity_id: int
    to_entity_id: int
    from_entity_code: str | None = None
    to_entity_code: str | None = None
    on_expr: str
    relationship_type: str
    join_type: str
    status: int
    completeness: CompletenessSchema | None = None


class TreeEntityNodeSchema(BaseModel):
    """实体节点(含下挂字段/维度/指标/知识)"""

    member_id: int = Field(..., description="成员记录ID(sem_model_entity)")
    role: str = Field(..., description="成员角色(primary/related)")
    sort: int = Field(..., description="展示顺序")
    id: int = Field(..., description="实体ID")
    code: str
    name: str
    entity_type: str | None = None
    status: int
    grain: str | None = None
    physical_table_id: int | None = None
    physical_datasource: str | None = None
    physical_database: str | None = None
    physical_table: str | None = None
    description: str | None = None
    completeness: CompletenessSchema | None = None
    fields: list[TreeFieldSchema] = []
    dimensions: list[TreeDimensionSchema] = []
    indicators: list[TreeIndicatorSchema] = []
    knowledge: list[TreeKnowledgeSchema] = []


class ModelTreeSchema(BaseModel):
    """模型树完整结构"""

    model: ModelOutSchema
    domain_name: str | None = None
    entities: list[TreeEntityNodeSchema] = []
    model_knowledge: list[TreeKnowledgeSchema] = Field(default=[], description="模型级知识(target_type=model)")
    joins: list[TreeJoinSchema] = Field(default=[], description="成员实体间JOIN边")
    model_completeness: CompletenessSchema | None = Field(default=None, description="模型节点完整度")
    publishable: bool = Field(default=False, description="是否可发布(树上无红灯)")
    publish_blockers: list[str] = Field(default=[], description="发布阻塞项(全部红灯)")


# ---- 发布快照 ----


class ReleaseOutSchema(BaseSchema):
    """发布版本列表项(不含快照正文)"""

    model_config = ConfigDict(from_attributes=True)

    model_id: int
    version: int
    node_stats: str | None = None
    checksum: str | None = None


class ReleaseDetailSchema(ReleaseOutSchema):
    """发布版本详情(含快照正文)"""

    snapshot: str


# ---- 查询参数 ----


@dataclass
class ModelQueryParam(BaseQueryParam):
    """语义模型查询参数"""

    code: str | None = None
    name: str | None = None
    domain_id: int | None = Query(None, gt=0, description="业务域ID")
    status: int | None = Query(None, ge=0, le=3, description="状态")

    def __post_init__(self) -> None:
        if self.code:
            self.code = (QueueEnum.like.value, self.code)
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
        if isinstance(self.domain_id, int):
            self.domain_id = (QueueEnum.eq.value, self.domain_id)
        if isinstance(self.status, int):
            self.status = (QueueEnum.eq.value, self.status)
