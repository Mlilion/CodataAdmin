"""MCP token 管理的出入参。

★ 出参**刻意不含明文 token**：`TokenOutSchema` 里没有 `token` 字段，
所以任何列表/详情接口在结构上就不可能把它漏出去。明文只走
`TokenCreatedSchema`（创建/轮换的返回），那是唯一一次能拿到它的机会。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.base_schema import BaseSchema


class TokenCreateSchema(BaseModel):
    """签发一个新 token"""

    name: str = Field(..., min_length=1, max_length=50, description="调用方名(进 query 日志的 caller)")
    bound_username: str | None = Field(
        default=None, max_length=50,
        description="绑定用户名(模型级隔离的身份; 留空则只能看 public 模型)",
    )
    expires_at: datetime | None = Field(default=None, description="过期时间(留空为长期有效)")
    description: str | None = Field(default=None, description="用途备注")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("调用方名不能为空")
        # caller 名进日志与统计，冒号会与 env 的 `name:token:username` 格式混淆
        if ":" in v or "," in v:
            raise ValueError("调用方名不能含冒号或逗号")
        return v

    @field_validator("bound_username")
    @classmethod
    def _strip_username(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None


class TokenUpdateSchema(BaseModel):
    """改元信息（不含 token 本身 —— 换 token 走 rotate）"""

    bound_username: str | None = Field(default=None, max_length=50, description="绑定用户名")
    expires_at: datetime | None = Field(default=None, description="过期时间")
    description: str | None = Field(default=None, description="用途备注")


class TokenOutSchema(BaseSchema):
    """token 元信息。★ 结构上不含明文，故读接口不可能漏出 token。"""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., description="调用方名")
    token_prefix: str = Field(default="", description="明文前若干位(仅用于认出是哪个 token)")
    bound_username: str | None = Field(default=None, description="绑定用户名")
    last_used_at: datetime | None = Field(default=None, description="最后使用时间")
    expires_at: datetime | None = Field(default=None, description="过期时间")
    revoked: bool = Field(default=False, description="是否已吊销")
    description: str | None = Field(default=None, description="用途备注")
    # 派生态：让前端不必自己拿 expires_at 与当前时间比（比错就会显示成可用）
    expired: bool = Field(default=False, description="是否已过期")
    active: bool = Field(default=True, description="当前是否可用(未吊销且未过期)")


class TokenCreatedSchema(TokenOutSchema):
    """创建/轮换的返回 —— **唯一**带明文的出参。"""

    token: str = Field(..., description="明文 token(仅此一次返回, 之后无法取回)")


class McpEndpointSchema(BaseModel):
    """MCP 接入信息 —— 页面上要能直接抄走的那几行。

    ★ 光有令牌不够：拿到钥匙的人还得知道门在哪。此前页面只给
    `Authorization: Bearer <token>`，URL 得回去翻文档，而文档里写的是
    `http://<host>:<port>/mcp` 这种占位符，仍然答不出"我这套部署填什么"。

    ★ 结构上不含任何令牌字段 —— 接入信息与令牌本体是两回事。
    """

    url: str = Field(..., description="MCP 端点完整地址(可直接粘进 client 配置)")
    source: str = Field(
        ...,
        description="地址来源: config=由 SEM_MCP_PUBLIC_URL 指定; request=由当前请求推导",
    )
    mounted: bool = Field(
        ...,
        description="该端点此刻是否真在服务(可能因开关或安全门禁未挂载)",
    )
    transport: str = Field(default="Streamable HTTP", description="传输方式")
    auth_header: str = Field(
        default="Authorization: Bearer <token>", description="鉴权头示例(占位符, 非真令牌)"
    )


class EnvTokenSchema(BaseModel):
    """env 里配的 token（只读，不可在页面吊销/轮换）。

    列出它们是为了让"当前到底有哪些 token 能进来"这个问题有完整答案 ——
    只显示库里的会让人以为 env 那些已经不生效了。
    """

    name: str = Field(..., description="调用方名")
    bound_username: str | None = Field(default=None, description="绑定用户名")
    source: str = Field(default="env", description="来源固定 env")
