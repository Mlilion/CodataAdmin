"""query 日志治理 schema。"""

from pydantic import BaseModel, ConfigDict, Field


class AliasCandidateOutSchema(BaseModel):
    """挖掘出的别名候选（待人工审核）。"""

    model_config = ConfigDict(from_attributes=True)

    alias: str = Field(description="候选别名(归一化后的用户说法)")
    node_type: str | None = Field(default=None, description="资产类型(indicator/dimension/...)")
    node_code: str = Field(description="归属资产 code(取实际采纳的那个)")
    hits: int = Field(description="被采纳次数")
    source: str = Field(default="mined", description="来源固定 mined")
    needs_review: bool = Field(default=True, description="待审(挖掘产物不自动获得权威性)")
    # ★ 复数：候选是 GROUP BY 聚合，同一说法可能被多个调用方问过。给单值会逼实现
    # "随便取一个" —— 看起来精确却是假的，比不给更糟。
    callers: list[str] = Field(default=[], description="贡献该候选的调用方(可能多个)")


class ZeroRecallOutSchema(BaseModel):
    """零召回的说法 —— 别名缺失最直接的证据。"""

    model_config = ConfigDict(from_attributes=True)

    query: str = Field(description="归一化后的问法")
    hits: int = Field(description="出现次数")
    callers: list[str] = Field(default=[], description="问过该说法的调用方(可能多个)")


class CallerOutSchema(BaseModel):
    """一个调用方及其提问量（给治理页的筛选框用）。

    没有这个清单，运营只能手打 caller 名 —— 而打错一个字得到的是空列表，
    空列表看起来像"这个调用方没问过东西"，不像"你打错了"。
    """

    model_config = ConfigDict(from_attributes=True)

    caller: str = Field(description="调用方名(来自 MCP token)")
    hits: int = Field(description="提问条数")


class AliasApproveSchema(BaseModel):
    """审核通过一条候选：把别名并进该资产的 aliases。

    ★ 只接受 alias + node_type + node_code 三元组，不接受"批量通过全部候选"——
    人工审核的价值就在于逐条看过。给一个 approve_all 等于把这道闸拆了。
    """

    alias: str = Field(min_length=1, max_length=100, description="要采纳的别名")
    node_type: str = Field(description="资产类型(indicator/dimension)")
    node_code: str = Field(min_length=1, max_length=100, description="归属资产 code")


class AliasApproveResult(BaseModel):
    """采纳结果。"""

    ok: bool = Field(description="是否写入成功")
    node_type: str = Field(description="资产类型")
    node_code: str = Field(description="资产 code")
    alias: str = Field(description="别名")
    already_present: bool = Field(default=False, description="该别名此前已存在(幂等重复采纳)")
    total_aliases: int = Field(default=0, description="该资产现有别名总数")
