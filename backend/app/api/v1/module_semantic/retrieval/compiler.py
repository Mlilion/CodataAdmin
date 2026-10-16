"""检索记录编译器（设计 §3.3 / §5.1；参考方案 §1）。

纯函数：Release 快照 + id/别名映射 → 检索记录 dict 列表。无 IO、无 DB，可单测、可 rebuild。

三个设计要点：
①`embed_text` 融合多信息做向量源（名称+别名+定义+可用维度+业务过程），让口语也能召回；
②`embed_hash` = md5(embed_text)[:32]，只在文本变化时重算 embedding；
③记录**不含** `sql_text` —— 权威口径只在快照里，索引副本存 SQL 会与之漂移。

全文与向量**用不同源文本**（参考方案 §2）：向量求语义丰富（拼全），
全文只放高信号短文本（name/别名高权重 + 描述截断），否则长描述稀释 BM25 排序。
"""

import hashlib

from .tokenizer import tokenize

# 全文源里描述类文本的截断长度：太长会稀释关键词排序
_FTS_DESC_LIMIT = 200
# 向量源里知识正文的截断长度
_EMBED_BODY_LIMIT = 500


def _join(parts: list[str | None]) -> str:
    """拼接非空片段。"""
    return " ".join(p.strip() for p in parts if p and p.strip())


def _hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:32]


def _record(
    *,
    node_type: str,
    node_id: int | None,
    node_code: str,
    name: str,
    synonyms: list[str],
    embed_text: str,
    fts_text: str,
    model_id: int,
    domain_id: int | None,
    release_version: int | None,
    lifecycle_status: int | None = None,
    usage_mode: str | None = None,
    needs_clarify: bool = False,
    not_buildable: bool = False,
) -> dict:
    """组装一条检索记录。tsv_tokens 是 jieba 分词结果，写库时交给 to_tsvector('simple')。"""
    return {
        "node_type": node_type,
        "node_id": node_id,
        "node_code": node_code,
        "name": name,
        "synonyms": synonyms,
        "embed_text": embed_text,
        "embed_hash": _hash(embed_text),
        "tsv_tokens": tokenize(fts_text),
        "model_ids": [model_id],
        "domain_id": domain_id,
        "usage_mode": usage_mode,
        "lifecycle_status": lifecycle_status,
        "needs_clarify": needs_clarify,
        "not_buildable": not_buildable,
        "release_version": release_version,
    }


def compile_records(
    snapshot: dict,
    *,
    ids: dict[tuple[str, str], int],
    aliases: dict[tuple[str, str], list[str]],
    model_id: int,
    domain_id: int | None = None,
    cross_domain: list[dict] | None = None,
    drafts: list[dict] | None = None,
) -> list[dict]:
    """把一份 Release 快照编译成检索记录列表。

    参数:
    - snapshot: Release 快照（权威已发布态）
    - ids: (node_type, code) → MySQL 主键。hydrate 时按 id 回查，故必须由调用方提供
           （快照只存 code，不存 id）。映射缺失的节点跳过 —— 写 node_id=None 会让
           hydrate 无法回查，不如不进索引
    - aliases: (node_type, code) → 别名列表。别名是检索层数据，改别名不该要求重新发布，
           故由调用方从活表提供
    - model_id / domain_id: 归属，用于检索期过滤
    - cross_domain: 跨域不可算指标（primary_entity_id=NULL，树与快照都没有）。编入并标
           not_buildable，命中即边界拒答（参考方案 Q14/Q15）
    - drafts: 待澄清口径指标（草稿/未审）。编入并标 needs_clarify，命中后走澄清
           而非当权威用（参考方案 Q11）
    """
    version = (snapshot.get("model") or {}).get("version")
    records: list[dict] = []

    def _alias(node_type: str, code: str) -> list[str]:
        return list(aliases.get((node_type, code)) or [])

    def _add(node_type: str, code: str, **kw) -> None:
        """按 id 映射补主键；缺失则跳过（不写 node_id=None）。"""
        node_id = ids.get((node_type, code))
        if node_id is None:
            return
        records.append(_record(
            node_type=node_type, node_id=node_id, node_code=code,
            model_id=model_id, domain_id=domain_id, release_version=version, **kw
        ))

    # 维度 code → 中文名：指标 embed_text 拼维度名（"订阅档位"）而非 code
    dim_names = {d["code"]: d.get("name") or d["code"] for d in snapshot.get("dimensions", [])}

    def _indicator(i: dict, *, not_buildable: bool = False, needs_clarify: bool = False) -> None:
        code = i["code"]
        syn = _alias("indicator", code)
        avail = [dim_names.get(c, c) for c in i.get("available_dimensions") or []]
        _add(
            "indicator", code,
            name=i.get("name") or code, synonyms=syn,
            embed_text=_join([
                i.get("name"),
                f"别名:{' '.join(syn)}" if syn else None,
                i.get("business_definition"),
                i.get("description"),
                i.get("unit"),
                f"可分析维度:{' '.join(avail)}" if avail else None,
            ]),
            # 全文源刻意精简：可用维度只进向量源，否则稀释关键词排序
            fts_text=_join([
                i.get("name"), " ".join(syn),
                (i.get("business_definition") or "")[:_FTS_DESC_LIMIT],
            ]),
            lifecycle_status=i.get("lifecycle_status"),
            not_buildable=not_buildable, needs_clarify=needs_clarify,
        )

    # ---- 指标（快照内已发布 + 两类特殊通道）----
    for i in snapshot.get("indicators", []):
        _indicator(i)
    for i in cross_domain or []:
        _indicator(i, not_buildable=True)
    for i in drafts or []:
        _indicator(i, needs_clarify=True)

    # ---- 维度 ----
    for d in snapshot.get("dimensions", []):
        code = d["code"]
        syn = _alias("dimension", code)
        binds = [
            f"{b.get('entity')}.{b.get('field')}"
            for b in d.get("bindings") or []
            if b.get("entity") or b.get("field")
        ]
        _add(
            "dimension", code,
            name=d.get("name") or code, synonyms=syn,
            embed_text=_join([
                d.get("name"),
                f"别名:{' '.join(syn)}" if syn else None,
                d.get("description"),
                d.get("dimension_type"),
                f"绑定:{' '.join(binds)}" if binds else None,
            ]),
            fts_text=_join([
                d.get("name"), " ".join(syn),
                (d.get("description") or "")[:_FTS_DESC_LIMIT],
            ]),
        )

    # ---- 实体 + 字段 ----
    for e in snapshot.get("entities", []):
        e_code = e["code"]
        phys = e.get("physical") or {}
        phys_parts = [phys.get("datasource"), phys.get("database"), phys.get("table")]
        _add(
            "entity", e_code,
            name=e.get("name") or e_code, synonyms=_alias("entity", e_code),
            embed_text=_join([
                e.get("name"), e.get("description"), e.get("entity_type"),
                f"粒度:{' '.join(e.get('grain') or [])}" if e.get("grain") else None,
                f"物理位置:{'.'.join(p for p in phys_parts if p)}" if any(phys_parts) else None,
            ]),
            fts_text=_join([e.get("name"), (e.get("description") or "")[:_FTS_DESC_LIMIT]]),
        )

        for f in e.get("fields", []):
            f_code = f["code"]
            enums = f.get("enum_values") or []
            enum_str = " ".join(str(v) for v in enums) if isinstance(enums, list) else str(enums)
            _add(
                "field", f_code,
                name=f.get("name") or f_code, synonyms=_alias("field", f_code),
                embed_text=_join([
                    f.get("name"), f.get("description"), f.get("field_role"), f.get("unit"),
                    f"枚举:{enum_str}" if enum_str else None,
                    f"所属:{e.get('name')}",
                ]),
                fts_text=_join([f.get("name"), (f.get("description") or "")[:_FTS_DESC_LIMIT]]),
            )

    # ---- 知识（三处来源：节点内联的 always/auto 摘要项、model_knowledge、knowledge_pool）----
    # pool 里是 auto 档的完整正文 —— P3 的核心缺口正是它此前不可达
    seen_kn: set[str] = set()

    def _knowledge(key: str, kn: dict) -> None:
        if key in seen_kn:
            return
        seen_kn.add(key)
        summary = kn.get("summary") or key
        _add(
            "knowledge", key,
            name=summary, synonyms=_alias("knowledge", key),
            embed_text=_join([
                summary,
                (kn.get("body") or "")[:_EMBED_BODY_LIMIT],
                kn.get("knowledge_type"),
            ]),
            fts_text=_join([summary, kn.get("tags")]),
            usage_mode=kn.get("usage_mode"),
        )

    for key, kn in (snapshot.get("knowledge_pool") or {}).items():
        _knowledge(key, kn)
    for kn in snapshot.get("model_knowledge") or []:
        _knowledge(kn["key"], kn)
    for e in snapshot.get("entities", []):
        for kn in e.get("knowledge") or []:
            _knowledge(kn["key"], kn)
        for f in e.get("fields", []):
            for kn in f.get("knowledge") or []:
                _knowledge(kn["key"], kn)
    for group in ("dimensions", "indicators", "joins"):
        for node in snapshot.get(group) or []:
            for kn in node.get("knowledge") or []:
                _knowledge(kn["key"], kn)

    return records
