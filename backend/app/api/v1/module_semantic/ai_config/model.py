"""嵌入模型配置（平台级单例）。

为什么独立建表：
- `module_ai` 的模型配置是 Redis 存 + 按用户（聊天模型的个人偏好）；
  嵌入模型是**平台级** —— 检索索引全局共用，每人一份会导致向量维度不一致。
- 系统参数表是 key-value，存不下一组带加密 api_key 的结构化配置。
"""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class SemAiConfigModel(ModelMixin, TenantMixin, UserMixin):
    """嵌入模型配置。平台级单例 —— service 层保证只有一行有效记录。"""

    __tablename__: str = "sem_ai_config"
    __table_args__: dict[str, str] = {"comment": "嵌入模型配置(平台级单例)"}
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="openai_like", comment="提供商(openai_like/tei)")
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="端点 Base URL")
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True, comment="API Key(Fernet 加密)")
    model: Mapped[str | None] = mapped_column(String(120), nullable=True, comment="模型名")
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=1024, comment="向量维度(改动需重建 pgvector 列)")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否启用向量路")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="备注")
