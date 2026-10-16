"""检索索引同步（设计 §5.2）。

写入语义的核心是**增量**：
- `embed_hash` 未变 → 保留已算好的 embedding（省钱，参考方案 §1 要点二）
- `embed_hash` 变了 → 置 embedding=NULL，进待嵌入队列

所有函数在总开关关闭时都是 no-op 并返回 `{"skipped": True}` —— 发布链路不能因为
检索副库关闭或不可达而失败。
"""

from sqlalchemy import text

from app.config.setting import settings
from app.core.logger import logger

from .database import retrieval_db_session

_SKIPPED = {"skipped": True, "inserted": 0, "updated": 0, "pruned": 0}

# tsv 由 tokens 经 to_tsvector('simple') 生成：jieba 已切好词，simple 只按空格切分
_UPSERT_NODE = text("""
INSERT INTO sem_retrieval_node
  (node_type, node_id, node_code, name, synonyms, embed_text, embed_hash, tsv,
   model_ids, domain_id, usage_mode, lifecycle_status, needs_clarify, not_buildable,
   release_version, updated_at)
VALUES
  (:node_type, :node_id, :node_code, :name, :synonyms, :embed_text, :embed_hash,
   to_tsvector('simple', :tsv_tokens),
   :model_ids, :domain_id, :usage_mode, :lifecycle_status, :needs_clarify, :not_buildable,
   :release_version, now())
ON CONFLICT (node_type, node_id) DO UPDATE SET
  node_code = EXCLUDED.node_code,
  name = EXCLUDED.name,
  synonyms = EXCLUDED.synonyms,
  embed_text = EXCLUDED.embed_text,
  embed_hash = EXCLUDED.embed_hash,
  tsv = EXCLUDED.tsv,
  -- ★ 归属合并而非覆写：维表实体、全局指标、知识本就跨模型复用，
  -- 覆写会让前一个模型的归属丢失，按 model_id 过滤检索时漏召回
  model_ids = (
    SELECT array_agg(DISTINCT m ORDER BY m)
    FROM unnest(sem_retrieval_node.model_ids || EXCLUDED.model_ids) AS m
  ),
  domain_id = EXCLUDED.domain_id,
  usage_mode = EXCLUDED.usage_mode,
  lifecycle_status = EXCLUDED.lifecycle_status,
  needs_clarify = EXCLUDED.needs_clarify,
  not_buildable = EXCLUDED.not_buildable,
  release_version = EXCLUDED.release_version,
  updated_at = now(),
  -- 文本没变就留着已算好的向量；变了则清空，交给异步补齐
  embedding = CASE
    WHEN sem_retrieval_node.embed_hash = EXCLUDED.embed_hash THEN sem_retrieval_node.embedding
    ELSE NULL
  END
RETURNING (xmax = 0) AS inserted
""")

_UPSERT_ALIAS = text("""
INSERT INTO sem_retrieval_alias
  (alias, node_type, node_id, node_code, source, is_deprecated, updated_at)
VALUES (:alias, :node_type, :node_id, :node_code, :source, false, now())
ON CONFLICT (alias, node_type, node_id) DO UPDATE SET
  node_code = EXCLUDED.node_code,
  source = EXCLUDED.source,
  is_deprecated = false,
  updated_at = now()
""")


def normalize_alias(alias: str) -> str:
    """别名归一化：小写 + 去首尾空格。写入与查询必须走同一个函数才能等值命中。"""
    return alias.strip().lower()


async def upsert_records(records: list[dict], *, prune_model_id: int | None = None) -> dict:
    """写入检索记录（幂等）。

    参数:
    - records: compiler.compile_records() 的输出
    - prune_model_id: 给定时，删除该模型下本轮未出现的陈旧记录 —— 否则已下线的
      指标会永远留在索引里被召回

    返回 {"inserted": n, "updated": n, "pruned": n}
    """
    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        return dict(_SKIPPED)
    if not records and prune_model_id is None:
        return {"skipped": False, "inserted": 0, "updated": 0, "pruned": 0}

    inserted = updated = pruned = 0
    async with retrieval_db_session() as session:
        for rec in records:
            row = (await session.execute(_UPSERT_NODE, rec)).first()
            if row and row[0]:
                inserted += 1
            else:
                updated += 1

        if prune_model_id is not None:
            keep = [(r["node_type"], r["node_id"]) for r in records]
            params = {
                "mid": prune_model_id,
                "types": [t for t, _ in keep] or [""],
                "node_ids": [i for _, i in keep] or [0],
            }
            # 先摘掉本模型的归属（共享节点仍属其他模型，不能整行删）
            await session.execute(
                text("""
                    UPDATE sem_retrieval_node
                    SET model_ids = array_remove(model_ids, :mid), updated_at = now()
                    WHERE :mid = ANY(model_ids)
                      AND NOT (node_type = ANY(:types) AND node_id = ANY(:node_ids))
                """),
                params,
            )
            # 再删真孤儿（已不属于任何模型）
            result = await session.execute(
                text("""
                    DELETE FROM sem_retrieval_node
                    WHERE model_ids IS NULL OR cardinality(model_ids) = 0
                """)
            )
            pruned = result.rowcount or 0

    logger.info(f"检索索引同步: +{inserted} ~{updated} -{pruned}")
    return {"skipped": False, "inserted": inserted, "updated": updated, "pruned": pruned}


async def sync_aliases(items: list[dict]) -> dict:
    """同步别名表。

    每个 item 需含 node_type / node_id / node_code / synonyms。
    整体替换该节点的别名集合：删掉的别名要真的消失（治理上改别名必须能生效）。
    """
    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        return dict(_SKIPPED)

    written = removed = 0
    async with retrieval_db_session() as session:
        for item in items:
            aliases = sorted({
                normalize_alias(a) for a in (item.get("synonyms") or []) if a and a.strip()
            })
            for alias in aliases:
                await session.execute(_UPSERT_ALIAS, {
                    "alias": alias,
                    "node_type": item["node_type"],
                    "node_id": item["node_id"],
                    "node_code": item["node_code"],
                    "source": item.get("source", "manual"),
                })
            written += len(aliases)

            # 本节点下已不存在的别名删除
            result = await session.execute(
                text("""
                    DELETE FROM sem_retrieval_alias
                    WHERE node_type = :t AND node_id = :i
                      AND NOT (alias = ANY(:keep))
                """),
                {"t": item["node_type"], "i": item["node_id"], "keep": aliases or [""]},
            )
            removed += result.rowcount or 0

    logger.info(f"检索别名同步: {written} 条, 清理 {removed} 条")
    return {"skipped": False, "written": written, "removed": removed}
