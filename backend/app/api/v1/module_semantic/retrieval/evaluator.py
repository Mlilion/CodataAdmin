"""检索质量评测器（R13，设计 §10.2；参考方案 §7）。

纯计算：金标用例 + 实际召回 → 判定 → 聚合报告。不含 IO，可单测。

指标取舍：
- `precision_at_1` 是**首要**指标 —— 元数据检索里召回一个错误但相似的指标，
  比漏召更危险（参考方案 §1）
- `confusion_rate` 单独统计易混淆对（arppu vs arpu 这种）
- `reject_accuracy` 与召回率同等重要（参考方案 §7 明确把拒答准确率列为金标指标）
- `*_core` 排除依赖别名的用例：别名冷启动是数据活，不该污染引擎本身的质量判断
"""

from contextlib import contextmanager
from dataclasses import dataclass, field

from app.config.setting import settings

from .eval_gold import GoldCase


@dataclass
class CaseOutcome:
    """单条用例的判定结果。"""

    case: GoldCase
    hits: list[str] = field(default_factory=list)
    hit_at_1: bool = False
    hit_at_k: bool = False
    reciprocal_rank: float = 0.0
    confused: bool = False
    top_score: float = 0.0
    """Top1 命中的分数（消融时看排序质量变化，召回率可能不变而分数下降）"""
    reject_correct: bool | None = None
    """仅 expect_reject 用例有值"""
    flag_correct: bool | None = None
    """仅 expect_flag 用例有值"""

    @property
    def query(self) -> str:
        return self.case.query

    @property
    def passed(self) -> bool:
        """整体是否通过（按用例类型取对应判据）。"""
        if self.case.expect_reject:
            return bool(self.reject_correct)
        if self.case.expect_flag:
            return bool(self.flag_correct)
        return self.hit_at_1 and not self.confused

    @classmethod
    def judge(
        cls,
        case: GoldCase,
        *,
        hits: list[str],
        rejected: bool = False,
        flags: dict[str, set[str]] | None = None,
        top_score: float = 0.0,
    ) -> "CaseOutcome":
        """判定一条用例。

        参数:
        - hits: 实际召回的 code，**按分降序**
        - rejected: 检索是否返回了结构化拒答
        - flags: code → 标记集合（not_buildable / needs_clarify）
        """
        out = cls(case=case, hits=list(hits), top_score=top_score)
        expect = set(case.expect)

        if expect:
            first_expected_idx = next(
                (i for i, code in enumerate(hits) if code in expect), None
            )
            out.hit_at_k = first_expected_idx is not None
            out.hit_at_1 = bool(hits) and hits[0] in expect
            if first_expected_idx is not None:
                out.reciprocal_rank = 1.0 / (first_expected_idx + 1)

            # 易混淆项排在期望之前 → 混淆失败
            if case.confusable_with and first_expected_idx is not None:
                confusable_idx = next(
                    (i for i, code in enumerate(hits) if code in set(case.confusable_with)),
                    None,
                )
                out.confused = (
                    confusable_idx is not None and confusable_idx < first_expected_idx
                )
            elif case.confusable_with and first_expected_idx is None:
                out.confused = any(c in set(case.confusable_with) for c in hits)

        if case.expect_reject:
            # 应拒答：实际零命中且给了拒答，才算对
            out.reject_correct = rejected and not hits

        if case.expect_flag:
            # 标记类：必须命中且带对标记 —— 拒答与"命中但标不可算"是不同的产品行为
            got = (flags or {}).get(hits[0] if hits else "", set())
            out.flag_correct = out.hit_at_1 and case.expect_flag in got

        return out


@dataclass
class EvalReport:
    """聚合报告。"""

    total: int = 0
    passed: int = 0
    recall_at_k: float = 0.0
    precision_at_1: float = 0.0
    mrr: float = 0.0
    confusion_rate: float = 0.0
    reject_accuracy: float = 0.0
    flag_accuracy: float = 0.0
    # 排除依赖别名的用例后的核心指标（引擎本身的质量）
    recall_at_k_core: float = 0.0
    precision_at_1_core: float = 0.0
    alias_dependent_total: int = 0
    alias_dependent_hit: int = 0
    mean_top_score: float = 0.0
    """命中用例的平均 Top1 分数 —— 消融时召回率不变但分数下降即说明该路贡献排序质量"""
    failures: list[CaseOutcome] = field(default_factory=list)

    def summary(self) -> str:
        """一行摘要，供 CI 日志与人读。"""
        return (
            f"总 {self.total} 条 通过 {self.passed} | "
            f"R@K={self.recall_at_k:.2%} P@1={self.precision_at_1:.2%} "
            f"MRR={self.mrr:.3f} 混淆={self.confusion_rate:.2%} | "
            f"拒答={self.reject_accuracy:.2%} 标记={self.flag_accuracy:.2%} | "
            f"核心(去别名依赖) R@K={self.recall_at_k_core:.2%} P@1={self.precision_at_1_core:.2%} | "
            f"别名依赖 {self.alias_dependent_hit}/{self.alias_dependent_total} | "
            f"均分={self.mean_top_score:.3f}"
        )


def _rate(hit: int, total: int) -> float:
    return hit / total if total else 0.0


def aggregate(outcomes: list[CaseOutcome]) -> EvalReport:
    """聚合判定结果。召回类与拒答类分开统计（混在一起会互相稀释）。"""
    report = EvalReport(total=len(outcomes))
    if not outcomes:
        return report

    recall_cases = [o for o in outcomes if o.case.expect and not o.case.expect_reject]
    reject_cases = [o for o in outcomes if o.case.expect_reject]
    flag_cases = [o for o in outcomes if o.case.expect_flag]
    confusable_cases = [o for o in recall_cases if o.case.confusable_with]
    core_cases = [o for o in recall_cases if not o.case.aliases_required]
    alias_cases = [o for o in recall_cases if o.case.aliases_required]

    report.passed = sum(1 for o in outcomes if o.passed)
    report.recall_at_k = _rate(sum(o.hit_at_k for o in recall_cases), len(recall_cases))
    report.precision_at_1 = _rate(sum(o.hit_at_1 for o in recall_cases), len(recall_cases))
    report.mrr = (
        sum(o.reciprocal_rank for o in recall_cases) / len(recall_cases)
        if recall_cases else 0.0
    )
    report.confusion_rate = _rate(
        sum(o.confused for o in confusable_cases), len(confusable_cases)
    )
    report.reject_accuracy = _rate(
        sum(bool(o.reject_correct) for o in reject_cases), len(reject_cases)
    )
    report.flag_accuracy = _rate(
        sum(bool(o.flag_correct) for o in flag_cases), len(flag_cases)
    )
    report.recall_at_k_core = _rate(sum(o.hit_at_k for o in core_cases), len(core_cases))
    report.precision_at_1_core = _rate(sum(o.hit_at_1 for o in core_cases), len(core_cases))
    report.alias_dependent_total = len(alias_cases)
    report.alias_dependent_hit = sum(o.hit_at_k for o in alias_cases)
    scored = [o for o in recall_cases if o.hit_at_1]
    report.mean_top_score = (
        sum(o.top_score for o in scored) / len(scored) if scored else 0.0
    )
    report.failures = [o for o in outcomes if not o.passed]

    return report


@contextmanager
def _toggled(**overrides):
    """临时改配置（消融实验用），退出时恢复原值。"""
    saved = {k: getattr(settings, k) for k in overrides}
    for key, value in overrides.items():
        setattr(settings, key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)


async def run_eval(
    cases: list[GoldCase],
    *,
    model_id: int | None = None,
    top_k: int = 5,
    today: str | None = None,
    disable_fts: bool = False,
    disable_alias: bool = False,
) -> EvalReport:
    """把金标集跑过真实检索引擎并出报告。

    参数:
    - model_id: 限定模型（None 则全库检索）
    - top_k: Recall@K 的 K
    - today: 相对时间参考日（固定它才能让含"上月"的用例结果稳定）
    - disable_fts / disable_alias: 消融实验开关 —— 关掉某条召回路看分数变化，
      量化各路贡献（参考方案 §7）
    """
    from .search import SemanticSearch

    # 标记类用例（not_buildable / needs_clarify）本就是草稿态，必须开 include_draft
    # 才召得到，否则测的是"草稿被正确排除"而非"边界拒答有据可依"
    search = SemanticSearch()
    outcomes: list[CaseOutcome] = []

    overrides: dict = {}
    if disable_fts:
        overrides["SEM_RETRIEVAL_FTS_ENABLE"] = False

    with _toggled(**overrides):
        for case in cases:
            result = await search.search(
                case.query,
                node_types=[case.expect_type] if case.expect_type else None,
                model_id=model_id,
                top_k=top_k,
                include_draft=bool(case.expect_flag),
                today=today,
                _disable_alias=disable_alias,
            )
            hits = [i.node_code for i in result.items]
            flags = {
                i.node_code: {
                    name
                    for name, on in (
                        ("not_buildable", i.not_buildable),
                        ("needs_clarify", i.needs_clarify),
                    )
                    if on
                }
                for i in result.items
            }
            outcomes.append(CaseOutcome.judge(
                case,
                hits=hits,
                rejected=result.rejected is not None or result.ambiguous is not None,
                flags=flags,
                top_score=result.items[0].score if result.items else 0.0,
            ))

    return aggregate(outcomes)
