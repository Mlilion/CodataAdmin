"""嵌入配置 schema。"""

from pydantic import BaseModel, ConfigDict, Field

_PROVIDERS = {"openai_like", "tei"}


class AiConfigUpdateSchema(BaseModel):
    """保存嵌入配置。

    `api_key` 留空 = 不改（否则前端每次保存都得重填密钥）。
    """

    provider: str = Field(default="openai_like", description="提供商(openai_like/tei)")
    base_url: str = Field(default="", max_length=255, description="端点 Base URL")
    api_key: str = Field(default="", description="API Key；留空表示不修改")
    model: str = Field(default="", max_length=120, description="模型名")
    dimensions: int = Field(default=1024, gt=0, le=8192, description="向量维度")
    enabled: bool = Field(default=False, description="是否启用向量路")
    description: str | None = Field(default=None, description="备注")


class AiConfigOutSchema(BaseModel):
    """读配置。★ 不返回明文 api_key，只给掩码。"""

    model_config = ConfigDict(from_attributes=True)

    provider: str = Field(default="openai_like", description="提供商")
    base_url: str = Field(default="", description="端点 Base URL")
    api_key_masked: str = Field(default="", description="API Key 掩码(仅末四位可见)")
    api_key_configured: bool = Field(default=False, description="是否已配置 Key")
    model: str = Field(default="", description="模型名")
    dimensions: int = Field(default=1024, description="向量维度")
    enabled: bool = Field(default=False, description="是否启用向量路")
    description: str | None = Field(default=None, description="备注")


class ResolvedAiConfig(BaseModel):
    """内部用（embedding 客户端），含明文 key。不对外暴露。"""

    provider: str = "openai_like"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    dimensions: int = 1024
    enabled: bool = False


class AiConfigSaveResult(BaseModel):
    """保存结果。维度变更时前端要提示"正在重建向量索引"。"""

    dimension_changed: bool = Field(default=False, description="维度是否变更")
    old_dimensions: int | None = Field(default=None, description="变更前维度")
    new_dimensions: int | None = Field(default=None, description="变更后维度")
    cleared: int = Field(default=0, description="被清空的向量数(维度变更时)")
