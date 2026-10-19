from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.enums import QueueEnum
from app.core.base_params import BaseQueryParam
from app.core.base_schema import BaseSchema

DatasourceType = Literal["starrocks", "mysql", "redshift"]


def _normalize_datasource_type(value):
    if isinstance(value, str):
        return value.strip().lower()
    return value


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
class DatasourceOutSchema(BaseSchema):
    """数据源响应"""

    model_config = ConfigDict(from_attributes=True)

    external_id: str | None = Field(default=None, description="DataCatalog 数据源ID")
    name: str = Field(..., description="数据源名称")
    ds_type: str | None = Field(default=None, description="类型")
    host: str | None = Field(default=None, description="主机")
    port: int | None = Field(default=None, description="端口")
    username: str | None = Field(default=None, description="连接账号")
    default_database: str | None = Field(default=None, description="默认库")
    description: str | None = Field(default=None, description="描述")
    source: str = Field(..., description="来源")
    synced_at: datetime | None = Field(default=None, description="最近同步时间")
    # 注意：password_enc 不输出


class DatasourceCreateSchema(BaseModel):
    """登记自采集数据源"""

    name: str = Field(..., min_length=1, max_length=100, description="数据源名称")
    ds_type: DatasourceType = Field(default="starrocks", description="类型(starrocks/mysql/redshift)")
    host: str = Field(..., min_length=1, max_length=200, description="主机")
    port: int = Field(..., gt=0, le=65535, description="端口(StarRocks 通常 9030; Redshift 通常 5439)")
    username: str = Field(..., min_length=1, max_length=100, description="连接账号")
    password: str = Field(default="", description="连接密码(加密存储)")
    default_database: str | None = Field(default=None, max_length=100, description="默认库")
    description: str | None = Field(default=None, description="描述")

    @field_validator("ds_type", mode="before")
    @classmethod
    def normalize_ds_type(cls, value):
        return _normalize_datasource_type(value)


class DatasourceUpdateSchema(BaseModel):
    """更新数据源(password 留空=不改)"""

    name: str | None = Field(default=None, max_length=100, description="数据源名称")
    ds_type: DatasourceType | None = Field(default=None, description="类型")
    host: str | None = Field(default=None, max_length=200, description="主机")
    port: int | None = Field(default=None, gt=0, le=65535, description="端口")
    username: str | None = Field(default=None, max_length=100, description="连接账号")
    password: str | None = Field(default=None, description="连接密码(留空不改)")
    default_database: str | None = Field(default=None, max_length=100, description="默认库")
    description: str | None = Field(default=None, description="描述")

    @field_validator("ds_type", mode="before")
    @classmethod
    def normalize_ds_type(cls, value):
        return _normalize_datasource_type(value)


class CollectRequestSchema(BaseModel):
    """触发自采集"""

    datasource_id: int = Field(..., gt=0, description="本地数据源ID")
    db_names: list[str] | None = Field(default=None, description="指定库(为空则采全部非系统库)")


class DatabaseOutSchema(BaseSchema):
    """库响应"""

    model_config = ConfigDict(from_attributes=True)

    datasource_id: int = Field(..., description="所属数据源ID")
    name: str = Field(..., description="库名")
    charset: str | None = Field(default=None, description="字符集")
    table_count: int = Field(..., description="表数量")
    synced_at: datetime | None = Field(default=None, description="最近同步时间")


class ColumnOutSchema(BaseSchema):
    """列响应"""

    model_config = ConfigDict(from_attributes=True)

    table_id: int = Field(..., description="所属表ID")
    ordinal: int = Field(..., description="序号")
    name: str = Field(..., description="列名")
    col_type: str | None = Field(default=None, description="类型")
    column_type_full: str | None = Field(default=None, description="完整类型")
    key_type: str | None = Field(default=None, description="键类型")
    is_nullable: bool = Field(..., description="是否可空")
    default_val: str | None = Field(default=None, description="默认值")
    comment: str | None = Field(default=None, description="列注释")
    description: str | None = Field(default=None, description="业务描述")


class TableOutSchema(BaseSchema):
    """表响应（含列）"""

    model_config = ConfigDict(from_attributes=True)

    database_id: int = Field(..., description="所属库ID")
    external_id: str | None = Field(default=None, description="DataCatalog 表ID")
    db_name: str = Field(..., description="库名")
    name: str = Field(..., description="表名")
    table_type: str | None = Field(default=None, description="表类型")
    row_count: int | None = Field(default=None, description="行数")
    data_size: int | None = Field(default=None, description="数据大小")
    comment: str | None = Field(default=None, description="表注释")
    description: str | None = Field(default=None, description="业务描述")
    physical_update_time: datetime | None = Field(default=None, description="物理表更新时间")
    synced_at: datetime | None = Field(default=None, description="最近同步时间")
    columns: list[ColumnOutSchema] = Field(default_factory=list, description="列列表")


class TableListItemSchema(BaseSchema):
    """表列表项（不含列，用于列表页）"""

    model_config = ConfigDict(from_attributes=True)

    database_id: int = Field(..., description="所属库ID")
    db_name: str = Field(..., description="库名")
    name: str = Field(..., description="表名")
    table_type: str | None = Field(default=None, description="表类型")
    row_count: int | None = Field(default=None, description="行数")
    comment: str | None = Field(default=None, description="表注释")
    synced_at: datetime | None = Field(default=None, description="最近同步时间")


# ---------------------------------------------------------------------------
# 同步请求
# ---------------------------------------------------------------------------
class SyncRequestSchema(BaseModel):
    """触发从 DataCatalog 同步"""

    external_datasource_id: str = Field(..., description="DataCatalog 数据源UUID")
    name: str | None = Field(default=None, description="数据源名称(本地登记用,可选)")
    db_names: list[str] | None = Field(default=None, description="指定库(为空则同步全部库的表结构需逐库,建议指定)")


class SyncResultSchema(BaseModel):
    """同步结果"""

    datasource_id: int = Field(..., description="本地数据源ID")
    databases: int = Field(..., description="同步库数")
    tables: int = Field(..., description="同步表数")
    columns: int = Field(..., description="同步列数")
    message: str = Field(..., description="说明")
    # 点了同步的人当场就该知道"这次检出了几条变更"，而不是去另一个页面找
    changes_detected: int = Field(default=0, description="本次检出的上游变更数")


class UpstreamChangeOutSchema(BaseSchema):
    """一条上游变更待办。"""

    model_config = ConfigDict(from_attributes=True)

    change_kind: str = Field(..., description="变更类型")
    db_name: str = Field(..., description="库名")
    table_name: str = Field(..., description="表名")
    column_name: str | None = Field(default=None, description="列名(表级变更为空)")
    before_value: str | None = Field(default=None, description="变更前值")
    after_value: str | None = Field(default=None, description="变更后值")
    impact_level: str = Field(..., description="影响级别(bound/sql_ref/table_only)")
    impact_detail: dict = Field(default={}, description="受影响对象清单")
    status: str = Field(..., description="状态(pending/resolved/ignored)")
    resolve_note: str | None = Field(default=None, description="处理说明")
    resolved_at: datetime | None = Field(default=None, description="处理时间")
    detected_at: datetime | None = Field(default=None, description="首次检出时间")
    last_seen_at: datetime | None = Field(default=None, description="最近一次仍看到")


class ChangeResolveSchema(BaseModel):
    """处理/忽略一条变更。

    ★ `ignored` 时 `resolve_note` 必填 —— 校验在 service 层做，
    因为"是 resolve 还是 ignore"由端点决定，schema 看不到。
    """

    resolve_note: str | None = Field(default=None, max_length=1000, description="处理说明")


# ---------------------------------------------------------------------------
# 查询参数
# ---------------------------------------------------------------------------
@dataclass
class TableQueryParam(BaseQueryParam):
    """表查询参数"""

    database_id: int | None = None
    db_name: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if self.database_id:
            self.database_id = (QueueEnum.eq.value, self.database_id)
        if self.db_name:
            self.db_name = (QueueEnum.eq.value, self.db_name)
        if self.name:
            self.name = (QueueEnum.like.value, self.name)
