"""物理层差异计算（纯函数，无 DB）。

只回答"物理层发生了什么"，不判断"谁受影响" —— 后者是 upstream_change 的事。
拆开的理由见设计 §三：差异检测与影响面判定会各自演化。
"""

from app.api.v1.module_semantic.catalog.change_draft import (
    ChangeKind,
    diff_columns,
)


def _incoming(*specs: tuple[str, str | None]) -> list[dict]:
    return [{"name": n, "col_type": t} for n, t in specs]


def test_new_column_produces_added_draft():
    drafts = diff_columns(
        "dws", "t_order", 1,
        existing={"amount": (10, "decimal", False)},
        incoming=_incoming(("amount", "decimal"), ("currency", "varchar")),
    )

    assert len(drafts) == 1
    d = drafts[0]
    assert (d.change_kind, d.column_name, d.after_value) == (
        ChangeKind.COLUMN_ADDED, "currency", "varchar",
    )
    assert (d.db_name, d.table_name, d.table_id) == ("dws", "t_order", 1)


def test_missing_column_produces_removed_draft():
    drafts = diff_columns(
        "dws", "t_order", 1,
        existing={"amount": (10, "decimal", False), "day": (11, "date", False)},
        incoming=_incoming(("amount", "decimal")),
    )

    assert len(drafts) == 1
    d = drafts[0]
    assert (d.change_kind, d.column_name, d.column_id) == (
        ChangeKind.COLUMN_REMOVED, "day", 11,
    )


def test_type_change_carries_before_and_after():
    drafts = diff_columns(
        "dws", "t_order", 1,
        existing={"amount": (10, "decimal", False)},
        incoming=_incoming(("amount", "varchar")),
    )

    assert len(drafts) == 1
    d = drafts[0]
    assert d.change_kind == ChangeKind.COLUMN_TYPE_CHANGED
    assert (d.before_value, d.after_value) == ("decimal", "varchar")


def test_unchanged_column_produces_nothing():
    drafts = diff_columns(
        "dws", "t_order", 1,
        existing={"amount": (10, "decimal", False)},
        incoming=_incoming(("amount", "decimal")),
    )

    assert drafts == []


def test_revived_column_produces_nothing():
    """★★ 列删了又加回来 → 净效果是"什么都没变"，不记 draft。

    若记成 column_added，人打开待办箱看到"新增了 day"会困惑 —— 它一直都在。
    这里刻意只看**净变化**，不做变更流水（设计 §5.1）。
    """
    drafts = diff_columns(
        "dws", "t_order", 1,
        existing={"day": (11, "date", True)},   # 上次同步时软删了
        incoming=_incoming(("day", "date")),     # 这次又出现了
    )

    assert drafts == []


def test_already_removed_column_is_not_reported_again():
    """已软删且这次仍未出现 → 不重复报（否则每日定时会一直刷同一条）。"""
    drafts = diff_columns(
        "dws", "t_order", 1,
        existing={"day": (11, "date", True)},
        incoming=_incoming(("amount", "decimal")),
    )

    kinds = [d.change_kind for d in drafts]
    assert ChangeKind.COLUMN_REMOVED not in kinds


def test_nameless_incoming_column_is_skipped():
    """载荷里没有 name 的条目直接跳过 —— 上游偶发脏数据不该让整次同步炸。"""
    drafts = diff_columns(
        "dws", "t_order", 1,
        existing={},
        incoming=[{"name": "", "col_type": "varchar"}, {"col_type": "int"}],
    )

    assert drafts == []


def test_draft_is_frozen():
    """draft 是不可变的事实记录 —— 判影响面时不该有人往里塞字段。"""
    import dataclasses

    import pytest

    drafts = diff_columns(
        "dws", "t_order", 1, existing={}, incoming=_incoming(("a_col", "int")),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        drafts[0].column_name = "hacked"
