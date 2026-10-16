"""indicator_completeness 纯函数单测(不依赖 DB)。

覆盖草稿/已发布两条路径:草稿缺主口径判黄(不拦发布),
已发布缺审核通过主口径判红(自相矛盾)。
"""
from types import SimpleNamespace

from app.api.v1.module_semantic.model.completeness import indicator_completeness

_DRAFT = 0
_PUBLISHED = 2


def _ind(lifecycle_status, available_dimension_ids="[1,2]", primary_entity_id=111):
    return SimpleNamespace(
        lifecycle_status=lifecycle_status,
        available_dimension_ids=available_dimension_ids,
        primary_entity_id=primary_entity_id,
    )


def _impl(role="secondary", review_status=0):
    return SimpleNamespace(role=role, review_status=review_status)


def test_draft_missing_primary_is_yellow():
    """草稿指标 + 无审核通过主口径 → 黄灯(判黄不拦发布),不得为红。"""
    res = indicator_completeness(_ind(_DRAFT), impls=[])
    assert res.light == "yellow"
    assert "草稿口径未审核通过" in res.missing

    # impls 存在但未通过(review_status=0)同样判黄
    res2 = indicator_completeness(_ind(_DRAFT), impls=[_impl(role="primary", review_status=0)])
    assert res2.light == "yellow"


def test_published_missing_approved_primary_is_red():
    """已发布指标 + 无审核通过主口径 → 红灯(自相矛盾)。"""
    res = indicator_completeness(_ind(_PUBLISHED), impls=[_impl(role="primary", review_status=0)])
    assert res.light == "red"
    assert "无审核通过的主口径" in res.missing
