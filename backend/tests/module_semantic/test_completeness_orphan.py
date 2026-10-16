"""Task A2: 实体节点完整度红灯——未绑物理表 / 物理表失效(orphan)。

`completeness.py` 是纯函数模块,`entity_completeness(e, fields, valid_table_ids)`
按已批量加载的 ORM 对象 + 有效表 id 集合判灯,无需 DB。故此处直接构造轻量
ORM 实例作为入参(对齐真实签名,brief 里的 dict 形状是示意)。

- physical_table_id 为空 → 🔴「未绑定物理表」
- physical_table_id 不在 valid_table_ids(cat_table is_deleted 或不存在) → 🔴「绑定物理表已失效(orphan)」
"""

from app.api.v1.module_semantic.entity.model import SemEntityModel, SemFieldModel
from app.api.v1.module_semantic.model.completeness import entity_completeness

_STATUS_PUBLISHED = 2


def _entity(**kw) -> SemEntityModel:
    base = {
        "code": "e",
        "name": "实体",
        "entity_type": "fact",
        "status": _STATUS_PUBLISHED,
        "grain": "id",
        "owner_id": 1,
    }
    base.update(kw)
    return SemEntityModel(**base)


def _valid_field() -> SemFieldModel:
    # 提供一个业务字段,隔离出「物理表」相关红灯(否则会叠加「无业务字段」红灯)
    return SemFieldModel(code="f", name="字段", field_role="dimension", data_type="string")


def test_entity_unbound_physical_is_red():
    """实体未绑物理表 → 红灯,missing 含物理表文案。"""
    comp = entity_completeness(_entity(physical_table_id=None), [_valid_field()], valid_table_ids=set())
    assert comp.light == "red"
    assert any("物理表" in m for m in comp.missing)


def test_entity_orphan_physical_is_red():
    """实体绑定的 cat_table 已失效(不在 valid_table_ids) → 红灯 orphan。"""
    comp = entity_completeness(_entity(physical_table_id=99), [_valid_field()], valid_table_ids={1, 2})
    assert comp.light == "red"
    assert any("orphan" in m or "失效" in m for m in comp.missing)


def test_entity_bound_valid_physical_not_red_for_table():
    """物理表有效时,不因物理表报红(对照组,防止误判)。"""
    comp = entity_completeness(_entity(physical_table_id=1), [_valid_field()], valid_table_ids={1, 2})
    assert not any("物理表" in m for m in comp.missing)
