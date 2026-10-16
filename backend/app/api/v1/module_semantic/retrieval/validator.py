"""查询意图校验器（P4-1；参考方案 §3⑥ 规则 1–8）。

**幻觉的最后一道闸**。LLM 唯一的自由动作是"NL → 结构化意图"，本模块用确定性规则
把它夹住：不合法就返回结构化错误让 agent 反思重试，而非放行一条错 SQL。

纯函数：输入意图 + hydrate 出的语义上下文，输出判定。无 IO、无 DB。

规则与 EVAL 失败模式的对应：
| 规则 | 防的失败模式 |
|---|---|
| 2 维度白名单 | 无效下钻（"退款金额按国家拆"） |
| 3 枚举值域 | 过滤值写错（order_type='sub'） |
| 4 时间窗必填 | **B1** 丢时间过滤算全表 |
| 5 draft 拦截 | **Q11** 草稿口径被当权威用 |
| 6 口径断层 | **Q10/B4** 跨 6/30 汇总虚高 2-3 倍（最危险） |
| 7 粒度护栏 | **B2** 订单级指标误用 DWD 明细 |

规则 8（只读账号 + LIMIT + EXPLAIN）属执行期，见 executor.py。
"""

import difflib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.core.logger import logger

_STATUS_PUBLISHED = 2
_REVIEW_APPROVED = 1

# 订单级/快照级指标不该落明细层（DWD 一订单多行，直接 SUM 会重复计）
_ORDER_GRAINS = {"order", "snapshot", "day"}
_DETAIL_LAYERS = {"DWD", "ODS"}
# 已知数据层。agent 声明的层必须在此集合内 —— 拼错若静默忽略，护栏就悄悄失效，
# 而 agent 会以为自己受到了保护
_KNOWN_LAYERS = {"ODS", "DWD", "DWS", "ADS", "DIM"}


def pick_primary(impls: list[dict] | None) -> dict | None:
    """选主口径：按 role 选，**不靠数组位置**。

    ★ 曾经是 `impls[0]`，但快照编译加载 impl 时没有 ORDER BY（顺序由数据库返回顺序
    决定），一旦某指标有多个审核通过的口径、secondary 恰好排在前面，就会拿副口径当权威
    口径去判时间窗/粒度/填参执行 —— 数字照样返回，口径已经错了，正是方案 §4 说的
    「最危险」那类（数据查得出、SQL 也没错，但口径错了）。

    参考方案 §5 说副口径「常是 ADS 预聚合层的高效物化」，即多口径是常态而非边缘。

    找不到 role=primary 时回退首项：宁可用一个口径继续（下游还有 EXPLAIN 兜底），
    也不要因为数据没标 role 就整体拒答。
    """
    if not impls:
        return None
    for impl in impls:
        if impl.get("role") == "primary":
            return impl
    return impls[0]


@dataclass
class QueryIntent:
    """LLM 输出的结构化意图（受 JSON Schema 约束）。"""

    indicator: str = ""
    dimensions: list[str] = field(default_factory=list)
    filters: list[dict] = field(default_factory=list)
    time: dict | None = None
    """{"field":..., "start":..., "end":...} 或 snapshot 类的 {"as_of":...}"""
    order_by: dict | None = None
    """{"field": 度量别名或分组维度 code, "direction": "asc"|"desc"}。排序在聚合后执行。"""
    top_n: int | None = None
    """排序后只取前 N 行（业务意图截取，非防扫描护栏）。必须与 order_by 同时给。"""


@dataclass
class ValidationError:
    """结构化错误 —— agent 据此反思重试，不是给人看的一句话。"""

    rule: str
    message: str
    allowed: list[str] = field(default_factory=list)
    """合法取值（维度白名单 / 枚举值），让 agent 知道该改成什么"""
    did_you_mean: list[str] = field(default_factory=list)
    needs_clarify: bool = False
    """需向用户澄清而非自行重试"""
    knowledge_key: str | None = None
    """相关知识条目，agent 可据此向用户解释"""

    def to_dict(self) -> dict:
        out: dict = {"rule": self.rule, "message": self.message}
        if self.allowed:
            out["allowed"] = self.allowed
        if self.did_you_mean:
            out["did_you_mean"] = self.did_you_mean
        if self.needs_clarify:
            out["needs_clarify"] = True
        if self.knowledge_key:
            out["knowledge_key"] = self.knowledge_key
        return out


@dataclass
class ValidationResult:
    ok: bool
    errors: list[ValidationError] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": [e.to_dict() for e in self.errors]}


# ---- 断层日期解析 ----

_DATE_IN_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _caliber_break_date(knowledge: list[dict]) -> tuple[date, str, str] | None:
    """取口径分水岭。返回 (最后一个旧口径日, 知识 key, 差异说明)。

    不硬编码日期：它是知识的一部分，换模型/换分水岭时靠改知识而非改代码。
    上下文没有该知识就不施加此规则。

    ★ 差一天的坑：bill 知识写「2026-06-29 revenue_usd 上线是分水岭」，但紧接着说
    「6/30 后 = 实付（权威）/ 6/30 前 = 名义价」—— 上线日是 6/29，语义分界是 6/30
    （6/30 及之后属新口径）。故此处一律取"**最后一个旧口径日**" = 上线日，
    判定用 `> break_date` 表示落在新口径区间。这样 6/30 单日查询被正确判为新口径。
    填 `caliber_break_date` 时也必须按这个语义填（前端表单已注明）。

    ## 两级判据：结构化字段优先，正文正则回退

    此前只有正则一条路，隐含三个**无校验的契约**：必须是 caveat 类、正文必须含
    "分水岭"/"口径断层"、取正文第一个 YYYY-MM-DD。任一被破坏则护栏**静默失效** ——
    不报错不告警，而失效的表现恰恰是"给出一个看起来完全合理的错数字"。真实风险：
    bill 那条知识的正文现在同时含 2026-06-29(真分水岭)与 2026-07-31(重构说明)，
    正则只取第一个匹配，日期顺序一变就拦错区间。

    ★ 回退路径**刻意保留**：直接删掉会让尚未回填字段的现有数据在回填前静默失去护栏
    —— 那正是本次要消除的失效模式。回退命中时打一条 info 提示回填，
    让"还在依赖措辞"这件事可被发现，而不是永久沉默。
    """
    # ① 结构化字段（唯一权威判据，不依赖类型与措辞）
    for kn in knowledge:
        if (raw := kn.get("caliber_break_date")) and (parsed := _parse_date(raw)):
            return parsed, kn["key"], (kn.get("caliber_break_note") or "")

    # ② 正文正则（兼容未回填的老数据）
    for kn in knowledge:
        if kn.get("knowledge_type") != "caveat":
            continue
        text = f"{kn.get('summary') or ''}\n{kn.get('body') or ''}"
        if "分水岭" not in text and "口径断层" not in text:
            continue
        if m := _DATE_IN_TEXT.search(text):
            logger.info(
                f"知识[{kn['key']}]的口径分水岭仍靠正文措辞识别 —— "
                f"建议回填 caliber_break_date 字段，否则改一句措辞就会让护栏静默失效"
            )
            return (
                date(int(m.group(1)), int(m.group(2)), int(m.group(3))),
                kn["key"],
                "",
            )
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def validate_intent(
    intent: QueryIntent,
    context: dict,
    *,
    data_layer: str | None = None,
    force_data_layer: str | None = None,
) -> ValidationResult:
    """校验结构化意图。

    参数:
    - context: SemanticHydrator.hydrate() 的输出（dict 形态）
    - data_layer: agent 声明「我要走哪个数据层」，供粒度护栏判定（规则 7）。
      ★ 纯护栏语义：只能导致**拒绝**，绝不改变执行哪条 SQL。声明与主口径不一致时
      拦下并说明实际是哪层 —— 若改成"按声明去挑对应 impl 执行"，就等于把选口径的
      权力交给 LLM，与 §3⑦「SQL 查表得到、LLM 碰不到」冲突。不传则退回读主口径自己的层。
    - force_data_layer: `data_layer` 的旧名，保留兼容既有调用。
    """
    declared_layer = data_layer if data_layer is not None else force_data_layer
    errors: list[ValidationError] = []
    indicators = {i["code"]: i for i in context.get("indicators") or []}
    dimensions = {d["code"]: d for d in context.get("dimensions") or []}
    knowledge = context.get("knowledge") or []

    # 字段表：code → field（跨实体合并；同名字段枚举一致是数据治理的事）
    fields: dict[str, dict] = {}
    for entity in context.get("entities") or []:
        for f in entity.get("fields") or []:
            fields.setdefault(f["code"], f)

    # ---- 规则1：指标存在性 ----
    ind = indicators.get(intent.indicator)
    if ind is None:
        close = difflib.get_close_matches(
            intent.indicator or "", list(indicators), n=3, cutoff=0.5
        )
        message = f"指标[{intent.indicator or '(空)'}]不存在于本模型的权威口径中"
        if close:
            # ★ 近似建议是"字面最像"，不是"口径等价"。实测问 arpu 会建议 arppu ——
            # 两者只差一个字母, 分母却完全不同(活跃用户 vs 付费用户)。agent 若顺着
            # 改用建议重试, 会把分母不同的数字当原指标报出去, 且看起来完全合理。
            # 不能靠提高 cutoff 区分: sub_revenu→sub_revenue 也只差一个字母, 那是
            # 真该建议的拼写错误。字面上无从区分, 只能在消息里要求先核对口径。
            message += (
                f"。字面相近的有 {close} —— 这只是**拼写相似**，不代表口径相同，"
                f"改用前须先 get_model_context 核对口径定义；"
                f"若口径不同应如实告知用户本模型没有该指标，不要用相近指标凑数"
            )
        errors.append(ValidationError(
            rule="indicator_exists",
            message=message,
            did_you_mean=close,
            allowed=sorted(indicators)[:20],
        ))
        return ValidationResult(ok=False, errors=errors)  # 后续规则都依赖指标，早退

    # ---- 规则5：draft 拦截（草稿口径走澄清，不当权威用）----
    if ind.get("lifecycle_status") != _STATUS_PUBLISHED:
        errors.append(ValidationError(
            rule="lifecycle_published",
            message=(
                f"指标[{ind['code']}]口径待确认，不可直接取数。"
                f"{ind.get('business_definition') or ''}"
            ),
            needs_clarify=True,
        ))

    impls = [
        im for im in (ind.get("impls") or [])
        if im.get("review_status") in (None, _REVIEW_APPROVED)
    ]
    if not impls:
        errors.append(ValidationError(
            rule="impl_available",
            message=f"指标[{ind['code']}]无审核通过的口径 SQL，无法取数",
        ))

    # ---- 规则2：维度白名单 ----
    allowed_dims = list(ind.get("available_dimensions") or [])
    for dim in intent.dimensions:
        if dim not in dimensions:
            errors.append(ValidationError(
                rule="dimension_exists",
                message=f"维度[{dim}]不存在于本模型",
                allowed=sorted(dimensions)[:20],
            ))
        elif dim not in allowed_dims:
            errors.append(ValidationError(
                rule="dimension_allowed",
                message=f"指标[{ind['code']}]不支持按[{dim}]拆分",
                allowed=allowed_dims,
            ))

    # ---- 规则3：过滤值域 ----
    for flt in intent.filters:
        fcode = flt.get("field")
        fld = fields.get(fcode)
        if fld is None:
            errors.append(ValidationError(
                rule="filter_field_exists",
                message=f"过滤字段[{fcode}]不存在于本模型",
                allowed=sorted(fields)[:20],
            ))
            continue
        enums = fld.get("enum_values")
        if not enums:
            continue  # 高基数字段无枚举：硬校验会误伤 country/city
        raw = flt.get("value")
        values = raw if isinstance(raw, list) else [raw]
        for v in values:
            if v is not None and str(v) not in [str(e) for e in enums]:
                errors.append(ValidationError(
                    rule="filter_enum",
                    message=f"[{fcode}]的取值[{v}]不合法",
                    allowed=[str(e) for e in enums],
                ))

    # ---- 规则4：时间窗必填 ----
    primary = pick_primary(impls)
    sql_text = (primary or {}).get("sql_text") or ""
    is_snapshot = "{as_of}" in sql_text

    tw = intent.time or {}
    start, end = _parse_date(tw.get("start")), _parse_date(tw.get("end"))
    as_of = _parse_date(tw.get("as_of"))

    if is_snapshot:
        if as_of is None and (start is None or end is None):
            errors.append(ValidationError(
                rule="time_required",
                message=f"指标[{ind['code']}]是时点类，需给出 as_of（或完整区间）",
            ))
    elif start is None or end is None:
        errors.append(ValidationError(
            rule="time_required",
            message=f"指标[{ind['code']}]必须给出时间窗（start/end），否则会全表扫描",
        ))
    elif start > end:
        errors.append(ValidationError(
            rule="time_range_valid",
            message=f"时间窗顺序颠倒：start({start}) 晚于 end({end})",
        ))

    # ---- 规则9：排序 / Top-N ----
    # 排序键只能是"指标本身"(按度量值排)或本次的分组维度之一。落在别处的字段既非
    # agent 该猜的物理列，也可能是新的注入面 —— 显式拒绝并告知合法取值。
    if intent.order_by is not None:
        ob_field = (intent.order_by or {}).get("field")
        sortable = [ind["code"], *intent.dimensions]
        if not ob_field:
            errors.append(ValidationError(
                rule="order_by_invalid",
                message="order_by 缺少 field（要按哪个字段排序）",
                allowed=sortable,
            ))
        elif ob_field not in sortable:
            errors.append(ValidationError(
                rule="order_by_invalid",
                message=(
                    f"排序字段[{ob_field}]不可用：只能按指标本身[{ind['code']}]"
                    f"（度量值）或本次的分组维度排序"
                ),
                allowed=sortable,
            ))
        raw_dir = str((intent.order_by or {}).get("direction", "desc")).strip().lower()
        if raw_dir not in ("asc", "desc"):
            errors.append(ValidationError(
                rule="order_by_invalid",
                message=f"排序方向[{raw_dir}]不合法，只能是 asc 或 desc",
                allowed=["asc", "desc"],
            ))

    if intent.top_n is not None:
        if intent.order_by is None:
            errors.append(ValidationError(
                rule="top_n_without_order",
                message=(
                    "top_n 需要 order_by 才有明确含义（"
                    "否则\"前 N 名\"按什么排序不确定）。请同时给出 order_by。"
                ),
            ))
        if not isinstance(intent.top_n, int) or isinstance(intent.top_n, bool) or intent.top_n <= 0:
            errors.append(ValidationError(
                rule="top_n_invalid",
                message=f"top_n 必须是正整数，收到 {intent.top_n!r}",
            ))

    # ---- 规则6：口径断层护栏 ----
    if break_info := _caliber_break_date(knowledge):
        break_date, kn_key, break_note = break_info
        # 新口径从 break_date 次日起生效（break_date 是最后一个旧口径日）
        new_caliber_from = break_date + timedelta(days=1)
        if start and end and start <= break_date:
            crossing = end > break_date
            # 差异说明优先用知识里的 note：原措辞"历史为名义价、直接汇总会显著虚高"
            # 是 bill 模型的具体情况，写死在通用校验器里意味着换模型就得改代码
            detail = break_note or "历史为名义价，直接汇总会显著虚高"
            errors.append(ValidationError(
                rule="caliber_break",
                message=(
                    f"时间窗{'跨越' if crossing else '完全早于'}口径分水岭"
                    f"（{new_caliber_from.isoformat()} 起为新口径），"
                    f"该区间数据与当前口径不可比（{detail}）。"
                    f"处理：限 {new_caliber_from.isoformat()} 之后，或明确标注口径切换点。"
                ),
                knowledge_key=kn_key,
            ))

    # ---- 规则7：粒度护栏 ----
    primary_layer = (primary or {}).get("data_layer")
    layer = primary_layer
    if declared_layer:
        normalized = str(declared_layer).strip().upper()
        if normalized not in _KNOWN_LAYERS:
            errors.append(ValidationError(
                rule="data_layer_unknown",
                message=f"未知数据层[{declared_layer}]",
                allowed=sorted(_KNOWN_LAYERS),
            ))
        else:
            layer = normalized
            # 声明与权威主口径不符 → 拦下并点出实际层。不静默换 SQL: 选哪条口径是
            # 权威侧的事, agent 只能被告知不符(否则 LLM 就能挑口径了)
            if primary_layer and normalized != str(primary_layer).strip().upper():
                errors.append(ValidationError(
                    rule="data_layer_mismatch",
                    message=(
                        f"指标[{ind['code']}]的权威主口径在 {primary_layer} 层，"
                        f"不是你声明的 {normalized} —— 本服务不按声明改换口径。"
                        f"若确需 {normalized} 层数据，请改用该层对应的指标。"
                    ),
                    allowed=[str(primary_layer)],
                ))

    grain = (primary or {}).get("granularity")
    if layer in _DETAIL_LAYERS and grain in _ORDER_GRAINS:
        errors.append(ValidationError(
            rule="grain_guard",
            message=(
                f"指标[{ind['code']}]是{grain}粒度，不可落{layer}明细层"
                f"（一订单多事件，直接聚合会重复计）"
            ),
            knowledge_key=next(
                (k["key"] for k in knowledge if "粒度" in (k.get("summary") or "")), None
            ),
        ))

    return ValidationResult(ok=not errors, errors=errors)
