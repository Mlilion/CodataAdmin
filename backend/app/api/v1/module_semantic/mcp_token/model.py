"""MCP 访问令牌（把 token 从 env 搬进库，可轮换、可吊销、可审计）。

## 为什么需要这张表

在此之前配 token 的**唯一**方式是改 `SEM_MCP_TOKENS` + 重启服务：

- 加一个接入方要改配置文件并重启（等于一次发布）
- token 明文躺在 env 里，谁能读到部署配置就能拿到全部 token
- **无法轮换**：换 token 要改 env 重启，期间所有接入方一起断
- **无法单独吊销**：只能整条配置项重写
- 查不到"这个 token 上次什么时候被用过" —— 于是没人敢删任何一个 token
  （不知道还有没有人在用），僵尸 token 只会越攒越多

## token 存哈希，不存密文

`token_hash` 存 SHA-256，**明文只在创建/轮换时返回一次**，之后任何接口都取不回。

★ 为何不用 `crypto_util`（Fernet 可逆加密，数据源密码走的是那条路）：那是为
"必须还原出明文去连外部系统"设计的。而 token 校验只需判断"你给的这串对不对" ——
存可逆密文纯属多余风险：`SECRET_KEY` 在 env 里，库备份 + env 泄露 = 全部 token 明文。
存哈希则库泄露也拿不到可用 token。

★ 代价是"忘了 token 只能轮换、不能查看"。这是对的取舍 —— 与其让人能查看，
不如让轮换足够便宜（一个按钮）。GitHub / Stripe 的 API key 都是这个语义。

★ 不用 bcrypt 而用 SHA-256：token 是 32 字节强随机串（`secrets.token_urlsafe`），
不是人选的密码 —— 没有字典攻击面，不需要慢哈希。而校验在**每个 MCP 请求**上跑，
bcrypt 的刻意慢会变成实打实的延迟。

`token_prefix` 存明文前 8 位，只为让人在列表里认出"这条是哪个 token"。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_model import ModelMixin, TenantMixin, UserMixin


class SemMcpTokenModel(ModelMixin, TenantMixin, UserMixin):
    """一个 MCP 接入方的访问令牌。"""

    __tablename__: str = "sem_mcp_token"
    __table_args__ = (
        # 鉴权路径按 token_hash 查，必须有索引（每个 MCP 请求都会走）
        Index("ix_sem_mcp_token_hash", "token_hash"),
        {"comment": "MCP 访问令牌"},
    )
    __loader_options__: list[str] = ["created_by", "updated_by", "deleted_by", "tenant_by"]

    name: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="调用方名(进 query 日志的 caller 字段)",
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="token 的 SHA-256(不可逆; 明文仅创建时返回一次)",
    )
    token_prefix: Mapped[str] = mapped_column(
        String(16), nullable=False, default="",
        comment="明文前若干位(仅用于在列表里认出是哪个 token)",
    )
    bound_username: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="绑定用户名(模型级隔离的身份; 留空则只能看 public 模型)",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True,
        comment="最后一次使用时间(没有它没人敢删任何 token)",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="过期时间(留空为长期有效)",
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True,
        comment="是否已吊销(吊销保留行以留审计痕迹, 不物理删)",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="用途备注")
