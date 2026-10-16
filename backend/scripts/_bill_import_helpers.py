"""bill 语义模型注入辅助:幂等 upsert + YAML 载入。"""
import os
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_SRC = Path(__file__).resolve().parent / "bill_semantic_model"
SRC = Path(os.getenv("BILL_MODEL_SRC") or _DEFAULT_SRC).expanduser()
TENANT_ID = 1
ADMIN_ID = 2


def load_yaml(name: str) -> dict:
    path = SRC / name
    if not path.is_file():
        raise FileNotFoundError(
            f"账单语义模型 YAML 不存在: {path}；"
            "请设置 BILL_MODEL_SRC 指向包含 L2_entity_field.yml 等文件的目录"
        )
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


async def upsert(
    db: AsyncSession,
    Model,
    match: dict[str, Any],
    values: dict[str, Any],
    *,
    preserve_if_none: tuple[str, ...] = (),
):
    """按 match 查存量(含软删),命中则更新 values 并复活,否则插入。返回 ORM 实例。

    `preserve_if_none` 列出的字段：传入 None 时**保留库中已有值**，不抹掉。

    为什么需要：本脚本按 YAML 全量重建字段，YAML 未声明的属性会传 None。枚举、别名
    这类**人工策展字段**（R12 补录 / 运营手补）不该被数据源重建静默抹掉 —— 曾真实发生：
    重跑注入把补录的 8 个枚举全冲掉了。
    """
    stmt = select(Model)
    for k, v in match.items():
        stmt = stmt.where(getattr(Model, k) == v)
    obj = (await db.execute(stmt)).scalars().first()
    if obj is None:
        obj = Model(**match, **values, tenant_id=TENANT_ID, created_id=ADMIN_ID, updated_id=ADMIN_ID)
        db.add(obj)
    else:
        for k, v in values.items():
            if v is None and k in preserve_if_none and getattr(obj, k, None) is not None:
                continue  # 保留人工策展成果
            setattr(obj, k, v)
        obj.is_deleted = False
        obj.updated_id = ADMIN_ID
    await db.flush()
    return obj
