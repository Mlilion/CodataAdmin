"""检索接口 schema。"""

from pydantic import BaseModel, Field, field_validator


class RebuildOutSchema(BaseModel):
    """索引重建结果"""

    models: int = Field(default=0, description="处理的模型数")
    inserted: int = Field(default=0, description="新增记录数")
    updated: int = Field(default=0, description="更新记录数")
    pruned: int = Field(default=0, description="清理的陈旧记录数")
    skipped: bool = Field(default=False, description="是否因总开关关闭而跳过")


class RetrievalStatusSchema(BaseModel):
    """检索副库状态（运维一眼看出索引是否在跑）"""

    enabled: bool = Field(..., description="总开关")
    reachable: bool = Field(..., description="副库是否可达")
    schema_ready: bool = Field(
        default=False, description="检索表是否已创建(与 reachable 分开: 连上了但没建表)"
    )
    node_count: int = Field(default=0, description="检索记录数")
    alias_count: int = Field(default=0, description="别名数")
    pending_embedding: int = Field(default=0, description="待生成向量的记录数")
    vector_dim: int | None = Field(default=None, description="pgvector 列实际宽度")
    dim_mismatch: bool = Field(
        default=False, description="列宽与嵌入配置维度不符(此状态下补向量会全部失败)"
    )
    fts_enabled: bool = Field(..., description="全文路开关")
    # 向量路有两个开关(env 部署级熔断 / DB 运营级启用)，分别报出才能定位该改哪个
    vector_enabled_env: bool = Field(default=False, description="查询期向量路开关(env)")
    vector_enabled_config: bool = Field(default=False, description="嵌入是否启用(AI 配置)")
    vector_effective: bool = Field(default=False, description="向量路最终是否生效")
    vector_inactive_reason: str = Field(default="", description="未生效的人话原因")
    vector_enabled: bool = Field(..., description="= vector_effective(兼容别名)")


class EmbedOutSchema(BaseModel):
    """嵌入补齐结果"""

    updated: int = Field(default=0, description="成功补齐的记录数")
    failed: int = Field(default=0, description="失败的记录数")
    skipped: bool = Field(default=False, description="是否因未启用而跳过")
    error: str | None = Field(default=None, description="错误说明")


class SearchRequestSchema(BaseModel):
    """检索请求"""

    query: str = Field(..., min_length=1, max_length=500, description="自然语言问法或 code")
    node_types: list[str] | None = Field(
        default=None, description="限定节点类型(indicator/dimension/knowledge/entity/field)"
    )
    model_id: int | None = Field(default=None, gt=0, description="限定语义模型")
    top_k: int = Field(default=10, ge=1, le=50, description="返回条数")
    include_draft: bool = Field(default=False, description="是否召回草稿态节点")

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("检索词不能为空")
        return v


class SearchItemSchema(BaseModel):
    """检索命中项。只返回 id + score，"它是什么" 由 hydrate 读快照回答。"""

    node_type: str = Field(..., description="节点类型")
    node_id: int = Field(..., description="MySQL 主键(hydrate 用)")
    node_code: str = Field(..., description="业务 code")
    name: str = Field(..., description="展示名")
    score: float = Field(..., description="分级基础分(精确1.00/0.97/0.93 模糊0.70-0.89 全文0.50-0.69)")
    match_level: str = Field(..., description="命中级别(exact/fuzzy/fts/vector)")
    usage_mode: str | None = Field(default=None, description="知识使用模式")
    lifecycle_status: int | None = Field(default=None, description="生命周期状态")
    needs_clarify: bool = Field(default=False, description="口径待确认, 应走澄清")
    not_buildable: bool = Field(default=False, description="本模型不可算, 应边界拒答")


class AmbiguousSchema(BaseModel):
    """歧义：多个高分候选难分，要求用 code 精确指定"""

    message: str = Field(..., description="澄清提示")
    candidates: list[SearchItemSchema] = Field(default_factory=list, description="并列候选")


class RejectedSchema(BaseModel):
    """结构化拒答：走澄清而非硬选"""

    reason: str = Field(..., description="拒答原因")
    did_you_mean: list[str] = Field(default_factory=list, description="近似建议 code")


class UnavailableSchema(BaseModel):
    """服务端不可用（与 rejected 平级：那是覆盖边界，这是故障）"""

    reason: str = Field(..., description="人类可读原因(不含内部细节)")
    retryable: bool = Field(default=True, description="是否值得重试")


class SearchResultSchema(BaseModel):
    """检索结果"""

    items: list[SearchItemSchema] = Field(default_factory=list, description="命中项(按分降序)")
    ambiguous: AmbiguousSchema | None = Field(default=None, description="歧义信息")
    rejected: RejectedSchema | None = Field(default=None, description="拒答信息(不在覆盖范围)")
    unavailable: UnavailableSchema | None = Field(
        default=None, description="服务端故障/未启用 —— 不是覆盖边界，不得据此说'不覆盖该指标'"
    )
    time_range: tuple[str, str] | None = Field(
        default=None, description="预处理剥出的时间窗(交给下游意图组装, 不参与语义匹配)"
    )
    keyword: str = Field(default="", description="归一化并剥掉时间词后的检索词")


class HydrateRequestSchema(BaseModel):
    """hydrate 请求：按检索命中的 code 取权威口径"""

    model_id: int = Field(..., gt=0, description="语义模型ID")
    indicator_codes: list[str] | None = Field(default=None, description="指标 code")
    dimension_codes: list[str] | None = Field(default=None, description="维度 code")
    entity_codes: list[str] | None = Field(default=None, description="实体 code")
    knowledge_keys: list[str] | None = Field(
        default=None, description="检索命中的知识 key(只影响 auto 档; always 无条件带)"
    )


class HydrateResultSchema(BaseModel):
    """agent 实际读到的语义上下文（全部来自 Release 快照，非活表）"""

    model_code: str = Field(default="", description="模型编码")
    model_name: str = Field(default="", description="模型名称")
    description: str | None = Field(default=None, description="分析场景说明")
    domain: str | None = Field(default=None, description="业务域")
    default_time_grain: str | None = Field(default=None, description="默认时间粒度")
    version: int | None = Field(default=None, description="快照版本(口径可追溯)")
    entities: list[dict] = Field(default_factory=list, description="实体(含物理位置/字段)")
    dimensions: list[dict] = Field(default_factory=list, description="维度(含绑定)")
    indicators: list[dict] = Field(default_factory=list, description="指标(含权威口径SQL)")
    joins: list[dict] = Field(default_factory=list, description="JOIN 关系")
    knowledge: list[dict] = Field(
        default_factory=list, description="always 全量 + 命中的 auto"
    )
    not_found: list[str] = Field(
        default_factory=list, description="点名了但快照里没有的 code"
    )
    error: str | None = Field(default=None, description="无快照等错误说明")
