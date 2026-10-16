"""EVAL 数值分执行器（评测方案 §三的第二半）。

口径分（`eval_bill.check_caliber`）只看产出 SQL 满不满足 checkpoints，**不需要真数据**；
数值分要拿权威口径真跑一遍，与 baseline 比对（误差 ≤1%，比率类 ≤1pp）。

## 为什么它此前不存在

需要一个能读 dws/dwd 的账号。`§13.12` 记的就是这件事 —— 于是 15 题里带 baseline 的
那 8 道从未被真正验证过，baseline 只是"写在那里的数"。

## 设计约束

★ **走 `query_indicator`，不自己拼 SQL**。数值分要回答的是"**权威口径**跑出的数对不对"，
所以必须经过与线上完全相同的链路（校验器 → 填参 → 只读执行 → LIMIT）。自己写 SQL
去比对等于换了个口径，测出来的结论无意义。

★ **偏差不等于失败，要能区分三类**：
  ① 权威 SQL 有 bug（如 Q8 的 `refund_time BETWEEN` 在 datetime 列上漏掉末日）
  ② 口径待定（如 Q7 升级单三个口径三个数）
  ③ baseline 该更新（数据本身变了）
执行器只负责给出**证据**（期望/实得/偏差率），归类要人做。所以它返回结构化结果而非
只给一个 pass/fail —— 把判断权留在有上下文的人手里。

★ **绝不为了让结果变绿去改 baseline**。那是把证据改成结论。
"""

from dataclasses import dataclass, field

from app.core.logger import logger

from .eval_bill import BILL_EVAL_CASES, EvalCase

# 数值容差：绝对量 1%（评测方案 value_tolerance=0.01）
_TOLERANCE = 0.01
# 比率类指标用绝对百分点，不用相对误差 —— 0.5% 与 0.505% 的相对误差是 1%，
# 但业务上这两个数没差别；反之 1% 与 2% 相对误差 100%，业务上是天壤之别
_RATE_TOLERANCE_PP = 0.01
_RATE_INDICATORS = {"refund_rate", "upgrade_rate", "renewal_rate", "pay_rate", "gross_margin"}


@dataclass
class NumericResult:
    """一道题的数值分结果。"""

    qid: str
    indicator: str
    passed: bool
    expected: float | dict | None = None
    actual: float | dict | None = None
    deviation: str = ""
    """人类可读的偏差描述（比率或差值）"""
    error: str = ""
    """未能取数时的原因（rule + message），空串表示取到数了"""
    skipped: bool = False
    """无 baseline 或非单指标题 —— 不算失败"""


@dataclass
class NumericReport:
    results: list[NumericResult] = field(default_factory=list)

    @property
    def ran(self) -> list[NumericResult]:
        return [r for r in self.results if not r.skipped]

    @property
    def failed(self) -> list[NumericResult]:
        return [r for r in self.ran if not r.passed]

    def summary(self) -> str:
        ran = self.ran
        ok = [r for r in ran if r.passed]
        lines = [f"数值分: {len(ok)}/{len(ran)} 通过（容差 {_TOLERANCE:.0%}）"]
        for r in ran:
            mark = "✅" if r.passed else "✗"
            detail = r.error or r.deviation
            lines.append(f"  {mark} {r.qid:4} {r.indicator:16} {detail}")
        return "\n".join(lines)


def _within(expected: float, actual: float, *, is_rate: bool) -> tuple[bool, str]:
    """比对单个数值，返回 (是否通过, 偏差描述)。"""
    if is_rate:
        diff = abs(actual - expected)
        return diff <= _RATE_TOLERANCE_PP, f"期望 {expected} 实得 {actual}（差 {diff:.4f}pp）"
    if expected == 0:
        return actual == 0, f"期望 0 实得 {actual}"
    rel = abs(actual - expected) / abs(expected)
    return rel <= _TOLERANCE, f"期望 {expected} 实得 {actual}（偏差 {rel:.2%}）"


def _compare_dict(expected: dict, actual_rows: list[dict]) -> tuple[bool, str]:
    """比对分组结果（如 Q5 按档位、Q8 金额+笔数）。

    ★ 缺档位也算失败：漏一个档位与算错一个档位对用户是同等严重的 —— 他看到的是一份
    不完整的拆分，却没有任何提示。
    """
    # 把行转成 {分组值: 数值} 或 {列名: 数值}
    flat: dict[str, float] = {}
    for row in actual_rows or []:
        vals = list(row.values())
        if len(vals) >= 2 and isinstance(vals[0], str):
            flat[str(vals[0])] = float(vals[1])
        else:
            for k, v in row.items():
                try:
                    flat[k] = float(v)
                except (TypeError, ValueError):
                    continue

    problems: list[str] = []
    for key, exp in expected.items():
        # Q8 的 {"amount":..,"cnt":..} 与 Q5 的 {"ultra":..} 都走这里；
        # 前者 key 是列名，后者是分组值，故两种都试
        got = flat.get(key)
        if got is None:
            matched = [v for k, v in flat.items() if key in k or k in key]
            got = matched[0] if matched else None
        if got is None:
            problems.append(f"{key} 缺失")
            continue
        ok, desc = _within(float(exp), got, is_rate=False)
        if not ok:
            problems.append(f"{key}: {desc}")
    extra = sorted(set(flat) - set(expected))
    if extra:
        problems.append(f"多出未预期分组 {extra}")
    return (not problems), ("；".join(problems) if problems else f"{len(expected)} 项全部匹配")


async def run_numeric_eval(
    model_id: int, *, cases: list[EvalCase] | None = None, query_fn=None
) -> NumericReport:
    """对带 baseline 的题跑数值分。

    参数:
    - model_id: 语义模型 ID
    - query_fn: 取数函数（默认 `tools.query_indicator`）。注入点仅为测试用 ——
      生产必须走真实链路，换成自拼 SQL 会让结论失去意义。
    """
    if query_fn is None:
        from .tools import query_indicator as query_fn  # noqa: N813

    report = NumericReport()
    for case in cases if cases is not None else BILL_EVAL_CASES:
        if case.baseline is None or not case.indicator:
            report.results.append(NumericResult(
                qid=case.qid, indicator=case.indicator or "-", passed=True, skipped=True,
            ))
            continue

        start, end = case.time_window
        # Q5 那类分组题需要带上维度，否则拿不到分档结果
        dims = ["dim_product_type"] if case.qid == "Q5" else None
        result = await query_fn(
            model_id=model_id, indicator=case.indicator,
            start=start, end=end, dimensions=dims,
        )

        if not result.get("ok"):
            err = (result.get("errors") or [{}])[0]
            report.results.append(NumericResult(
                qid=case.qid, indicator=case.indicator, passed=False,
                expected=case.baseline,
                error=f"{err.get('rule', '?')}: {str(err.get('message', ''))[:120]}",
            ))
            continue

        rows = result.get("rows") or []
        if isinstance(case.baseline, dict):
            ok, desc = _compare_dict(case.baseline, rows)
            actual: float | dict | None = {
                str(list(r.values())[0]): float(list(r.values())[1])
                for r in rows if len(r) >= 2 and isinstance(list(r.values())[0], str)
            } or (rows[0] if rows else None)
        else:
            raw = list(rows[0].values())[0] if rows and rows[0] else None
            if raw is None:
                ok, desc, actual = False, "结果为空", None
            else:
                actual = float(raw)
                ok, desc = _within(
                    float(case.baseline), actual,
                    is_rate=case.indicator in _RATE_INDICATORS,
                )

        report.results.append(NumericResult(
            qid=case.qid, indicator=case.indicator, passed=ok,
            expected=case.baseline, actual=actual, deviation=desc,
        ))

    if report.failed:
        logger.warning(f"EVAL 数值分有 {len(report.failed)} 题未通过（见 summary）")
    return report
