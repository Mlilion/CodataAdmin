"""对真实库跑检索金标集评测（R13）。

用法（从 backend/ 跑）：
    ENVIRONMENT=dev SEMANTIC_RETRIEVAL_ENABLE=true \
      SEM_PG_HOST=127.0.0.1 SEM_PG_PORT=5433 SEM_PG_USER=semantic \
      SEM_PG_PASSWORD=xxx SEM_PG_DB=semantic_retrieval \
      .venv/bin/python -m scripts.eval_retrieval [--model commercial_revenue] [--reindex]

输出：整体指标 + 失败用例清单 + 消融实验（各召回路贡献）。
失败清单是行动依据 —— 只给一个总分却不说该改什么，等于没测。
"""

import argparse
import asyncio
import os
import sys

# 固定相对时间参考日：否则含"上月"的用例结果随运行日期漂移
_EVAL_TODAY = "2026-07-31"


async def _run() -> int:
    from sqlalchemy import select

    from app.api.v1.module_semantic.model.model import SemModelModel
    from app.api.v1.module_semantic.retrieval.eval_gold import GOLD_CASES
    from app.api.v1.module_semantic.retrieval.evaluator import run_eval
    from app.api.v1.module_semantic.retrieval.indexer import RetrievalIndexer
    from app.core.base_model import MappedBase
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session
    from app.utils.import_util import ImportUtil

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="commercial_revenue", help="模型 code；留空则全库检索")
    parser.add_argument("--reindex", action="store_true", help="先重建索引")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    # standalone 入口需注册跨模块 mapper（照搬 app/alembic/env.py）
    ImportUtil.find_models(MappedBase)

    model_id: int | None = None
    async with async_db_session() as db:
        auth = AuthSchema(db=db, check_data_scope=False)
        if args.model:
            model_id = (await db.execute(
                select(SemModelModel.id).where(SemModelModel.code == args.model)
            )).scalar_one_or_none()
            if model_id is None:
                print(f"✗ 找不到模型 code={args.model}")
                return 1
        if args.reindex:
            stats = await RetrievalIndexer(auth).rebuild_all()
            print(f"索引重建: {stats}\n")

    report = await run_eval(
        GOLD_CASES, model_id=model_id, top_k=args.top_k, today=_EVAL_TODAY
    )

    print("=" * 78)
    print(f"检索金标集评测（模型={args.model or '全库'}, K={args.top_k}）")
    print("=" * 78)
    print(report.summary())

    if report.failures:
        print(f"\n失败用例 {len(report.failures)} 条：")
        for f in report.failures:
            kind = (
                "应拒答" if f.case.expect_reject
                else f"应带{f.case.expect_flag}" if f.case.expect_flag
                else "混淆" if f.confused
                else "未命中"
            )
            alias_note = " [依赖别名]" if f.case.aliases_required else ""
            print(f"  [{kind}]{alias_note} 「{f.case.query}」")
            print(f"      期望={f.case.expect or '(拒答)'}  实际={f.hits[:3] or '(空)'}")

    # 消融：关掉某条路看分数变化，量化其贡献（参考方案 §7）
    print("\n" + "-" * 78)
    print("消融实验（关掉某条召回路后的核心指标）")
    print("-" * 78)
    base = report
    for label, kw in (("关别名路", {"disable_alias": True}), ("关全文路", {"disable_fts": True})):
        ablated = await run_eval(
            GOLD_CASES, model_id=model_id, top_k=args.top_k, today=_EVAL_TODAY, **kw
        )
        delta_r = ablated.recall_at_k - base.recall_at_k
        delta_p = ablated.precision_at_1 - base.precision_at_1
        delta_s = ablated.mean_top_score - base.mean_top_score
        # 均分变化很关键：某条路可能不影响召回率，只影响排序质量
        # （别名同时写进 embed_text，关掉别名路后模糊路仍能召回，但分数从 0.93 掉到 0.75）
        print(
            f"  {label}: R@K={ablated.recall_at_k:.2%} ({delta_r:+.2%})  "
            f"P@1={ablated.precision_at_1:.2%} ({delta_p:+.2%})  "
            f"均分={ablated.mean_top_score:.3f} ({delta_s:+.3f})"
        )

    return 0


def main() -> None:
    env = os.getenv("ENVIRONMENT", "")
    if env not in ("dev", "test"):
        print(f"✗ 拒绝执行：ENVIRONMENT={env!r}，评测只在 dev/test 跑（会触碰索引）")
        sys.exit(1)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
