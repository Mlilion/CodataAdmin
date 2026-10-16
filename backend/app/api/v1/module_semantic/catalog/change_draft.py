"""物理层差异的纯描述（不含影响面判定）。

## 为什么单独一个模块

差异检测回答"物理层发生了什么"（表/列的增删与类型变更），影响面判定回答
"谁受影响"（哪些语义字段/口径/知识）。两者会各自演化 —— 前者将来可能加
"索引变了""分区变了"，后者可能改判据精度。混在一起会让 `catalog/service.py`
继续膨胀（现已 357 行）。

本模块**无任何依赖**，纯函数 + 不可变数据类，可单测到底。
"""

from dataclasses import dataclass


class ChangeKind:
    """变更类型。用类常量而非 Enum —— 它要直接进数据库 varchar 列并出现在 API 出参里，
    Enum 会让每处都得 `.value`，收益为零。"""

    COLUMN_ADDED = "column_added"
    COLUMN_REMOVED = "column_removed"
    COLUMN_TYPE_CHANGED = "column_type_changed"
    TABLE_REMOVED = "table_removed"


@dataclass(frozen=True)
class ChangeDraft:
    """一条"物理层变了什么"的事实记录。

    `frozen=True`：这是事实，判影响面时不该有人往里塞字段（那会让"事实"与
    "解读"混在一个对象里，下一个人无法分辨哪些字段可信）。
    """

    change_kind: str
    db_name: str
    table_name: str
    column_name: str | None = None
    table_id: int | None = None
    column_id: int | None = None
    before_value: str | None = None
    after_value: str | None = None


def diff_columns(
    db_name: str,
    table_name: str,
    table_id: int,
    existing: dict[str, tuple[int, str | None, bool]],
    incoming: list[dict],
) -> list[ChangeDraft]:
    """算出列级净变化。

    - `existing`：`{列名: (column_id, col_type, is_deleted)}`，**含软删行**
    - `incoming`：上游这一轮返回的列载荷

    ★ 只看**净变化**：
    - 软删的列这次又出现 → 复活，不记 draft（它一直都在，报"新增"会让人困惑）
    - 已软删且这次仍未出现 → 不重复报（否则每日定时会一直刷同一条）
    """
    drafts: list[ChangeDraft] = []
    seen: set[str] = set()

    for c in incoming:
        name = (c.get("name") or "").strip()
        if not name:
            continue  # 上游偶发脏数据不该让整次同步炸
        seen.add(name)
        col_type = c.get("col_type")
        old = existing.get(name)

        if old is None:
            drafts.append(ChangeDraft(
                change_kind=ChangeKind.COLUMN_ADDED,
                db_name=db_name, table_name=table_name, table_id=table_id,
                column_name=name, after_value=col_type,
            ))
            continue

        old_id, old_type, old_deleted = old
        if old_deleted:
            continue  # 复活：净效果是"什么都没变"
        if old_type != col_type:
            drafts.append(ChangeDraft(
                change_kind=ChangeKind.COLUMN_TYPE_CHANGED,
                db_name=db_name, table_name=table_name, table_id=table_id,
                column_name=name, column_id=old_id,
                before_value=old_type, after_value=col_type,
            ))

    for name, (col_id, col_type, old_deleted) in existing.items():
        if name in seen or old_deleted:
            continue  # 已软删的不重复报
        drafts.append(ChangeDraft(
            change_kind=ChangeKind.COLUMN_REMOVED,
            db_name=db_name, table_name=table_name, table_id=table_id,
            column_name=name, column_id=col_id, before_value=col_type,
        ))

    return drafts
