"""发布快照的目标方言兼容性审计。"""

from collections.abc import Iterable

import sqlglot
from sqlglot import exp

_SOURCE_DIALECT = "starrocks"
_REDSHIFT_UNSUPPORTED_FUNCS = frozenset({
    "unix_timestamp",
    "map_get",
    "bitmap_union_count",
    "hll_count",
})


def _iter_impls(snapshot: dict) -> Iterable[tuple[str, dict]]:
    for ind in snapshot.get("indicators") or []:
        for impl in ind.get("impls") or []:
            yield str(ind.get("code") or ""), impl


def audit_snapshot_compatibility(snapshot: dict, target_dialect: str) -> list[str]:
    """审计发布快照是否能兼容目标 SQL 方言。"""
    if str(target_dialect).strip().lower() != "redshift":
        return []

    problems: list[str] = []
    for ind_code, impl in _iter_impls(snapshot):
        sql_text = (impl.get("sql_text") or "").strip()
        if not sql_text:
            continue

        role = impl.get("role") or "primary"
        try:
            transpiled = sqlglot.transpile(
                sql_text,
                read=_SOURCE_DIALECT,
                write="redshift",
            )[0]
        except Exception as exc:  # noqa: BLE001
            problems.append(f"指标[{ind_code}]的{role}口径无法转成 Redshift SQL: {exc}")
            continue

        try:
            tree = sqlglot.parse_one(transpiled, dialect="redshift")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"指标[{ind_code}]的{role}口径转写后仍无法被 Redshift 解析: {exc}")
            continue

        unsupported = sorted(
            {
                func.name.lower()
                for func in tree.find_all(exp.Anonymous)
                if func.name and func.name.lower() in _REDSHIFT_UNSUPPORTED_FUNCS
            }
        )
        if unsupported:
            problems.append(
                f"指标[{ind_code}]的{role}口径使用了 Redshift 不支持的函数: {', '.join(unsupported)}"
            )
    return problems
