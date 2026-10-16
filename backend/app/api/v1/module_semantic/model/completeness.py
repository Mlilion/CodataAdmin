"""模型树完整度计算（设计文档 §四：树上的灯）。

纯函数：输入树组装时已批量加载的 ORM 对象与有效性集合，输出每节点 light/missing。
红灯定义见设计：未绑定/orphan/无审核通过主口径/端点未发布/知识未发布；
黄灯：可用但不完整（缺粒度/负责人/未发布/未声明维度等）。
模型可发布 = 全树无红灯。
"""

from ..dimension.model import SemDimensionModel
from ..entity.model import SemEntityModel, SemFieldModel
from ..indicator.model import SemIndicatorImplModel, SemIndicatorModel
from ..join.model import SemJoinModel
from ..knowledge.model import SemKnowledgeModel
from .model import SemModelModel
from .schema import CompletenessSchema

_STATUS_PUBLISHED = 2


def _result(red: list[str], yellow: list[str]) -> CompletenessSchema:
    if red:
        return CompletenessSchema(light="red", missing=red)
    if yellow:
        return CompletenessSchema(light="yellow", missing=yellow)
    return CompletenessSchema(light="green", missing=[])


def entity_completeness(e: SemEntityModel, fields: list[SemFieldModel], valid_table_ids: set[int]) -> CompletenessSchema:
    red: list[str] = []
    yellow: list[str] = []
    if not e.physical_table_id:
        red.append("未绑定物理表")
    elif e.physical_table_id not in valid_table_ids:
        red.append("绑定物理表已失效(orphan)")
    if not fields:
        red.append("无业务字段")
    if not e.grain:
        yellow.append("缺粒度(grain)")
    if not e.owner_id:
        yellow.append("缺负责人")
    if e.status != _STATUS_PUBLISHED:
        yellow.append("实体未发布")
    return _result(red, yellow)


def field_completeness(f: SemFieldModel, valid_column_ids: set[int]) -> CompletenessSchema:
    red: list[str] = []
    yellow: list[str] = []
    if not f.physical_column_id and not f.physical_column and not f.expr:
        red.append("未绑定物理列且无表达式")
    elif f.physical_column_id and f.physical_column_id not in valid_column_ids:
        red.append("绑定物理列已失效(orphan)")
    if not f.data_type:
        yellow.append("缺语义类型")
    if f.field_role == "measure" and not f.unit:
        yellow.append("度量缺单位")
    return _result(red, yellow)


def dimension_completeness(
    d: SemDimensionModel, entity: SemEntityModel | None, entity_field_codes: set[str]
) -> CompletenessSchema:
    red: list[str] = []
    yellow: list[str] = []
    if not d.bind_entity_id or not d.bind_field_code:
        red.append("未绑定实体字段")
    elif entity is None:
        red.append("绑定实体不存在")
    elif d.bind_field_code not in entity_field_codes:
        red.append(f"绑定字段[{d.bind_field_code}]在实体中不存在")
    else:
        if entity.status != _STATUS_PUBLISHED:
            yellow.append("目标实体未发布")
    if d.status != _STATUS_PUBLISHED:
        yellow.append("维度未发布")
    return _result(red, yellow)


def indicator_completeness(ind: SemIndicatorModel, impls: list[SemIndicatorImplModel]) -> CompletenessSchema:
    red: list[str] = []
    yellow: list[str] = []
    approved_primary = any(i.role == "primary" and i.review_status == 1 for i in impls)
    if not approved_primary:
        # 已发布指标缺审核通过主口径 = 真红(自相矛盾);草稿/待审指标本就不进快照,判黄不拦发布
        if ind.lifecycle_status == _STATUS_PUBLISHED:
            red.append("无审核通过的主口径")
        else:
            yellow.append("草稿口径未审核通过")
    if not ind.available_dimension_ids:
        yellow.append("未声明可用维度")
    if not ind.primary_entity_id:
        yellow.append("未设主实体")
    if ind.lifecycle_status != _STATUS_PUBLISHED:
        yellow.append("指标未发布")
    return _result(red, yellow)


def join_completeness(j: SemJoinModel, from_entity: SemEntityModel | None, to_entity: SemEntityModel | None) -> CompletenessSchema:
    red: list[str] = []
    if from_entity is None or to_entity is None:
        red.append("端点实体缺失")
    elif from_entity.status != _STATUS_PUBLISHED or to_entity.status != _STATUS_PUBLISHED:
        red.append("端点实体未发布")
    return _result(red, [])


def knowledge_completeness(kn: SemKnowledgeModel) -> CompletenessSchema:
    if kn.status != _STATUS_PUBLISHED:
        return CompletenessSchema(light="red", missing=["知识未发布"])
    return CompletenessSchema(light="green", missing=[])


def model_completeness(model: SemModelModel, member_entities: list[SemEntityModel]) -> CompletenessSchema:
    if not member_entities:
        return CompletenessSchema(light="red", missing=["无成员实体"])
    yellow: list[str] = []
    unpublished = [e.name for e in member_entities if e.status != _STATUS_PUBLISHED]
    if unpublished:
        yellow.append(f"{len(unpublished)} 个实体未发布: {'/'.join(unpublished[:3])}")
    if not model.description:
        yellow.append("缺场景说明")
    return _result([], yellow)
