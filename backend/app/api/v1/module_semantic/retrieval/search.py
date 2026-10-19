"""分级检索 + 拒答（设计 §6.1/§6.3；参考方案 §3②③）。

采用**分级基础分**而非 RRF —— 参考方案明确推荐：分级更适合"精确优先"，
更好解释、更可控。元数据检索追求精确率：召回一个错误但相似的指标，
比漏召更危险。

| 级别 | 手段 | 分数区间 |
|---|---|---|
| 精确 | code=1.00 / name=0.97 / 别名=0.93 |
| 模糊 | ILIKE 子串（多 token AND）| 0.70–0.89 |
| 全文 | jieba + ts_rank_cd 归一化 | 0.50–0.69 |

递进执行，同一节点多路命中取**最高分**（不累加）。
"""

from dataclasses import dataclass, field

from sqlalchemy import text

from app.config.setting import settings
from app.core.logger import logger

from .database import retrieval_db_session
from .preprocess import preprocess
from .sync import normalize_alias
from .tokenizer import tokenize

# 分级基础分
_SCORE_CODE = 1.00
_SCORE_NAME = 0.97
_SCORE_ALIAS = 0.93
_SCORE_FUZZY_CODE = 0.89
_SCORE_FUZZY_NAME = 0.86
_SCORE_FUZZY_MULTI = 0.86
_SCORE_FUZZY_EMBED = 0.75
_FTS_BASE = 0.50
_FTS_SPAN = 0.19  # 0.50–0.69
_VEC_BASE = 0.30
_VEC_SPAN = 0.19  # 0.30–0.49，第四级，永不盖过前三路

# 歧义判定（参考方案 §3③ 生产规则）
_AMBIGUOUS_FLOOR = 0.9
_AMBIGUOUS_GAP = 0.03

_STATUS_PUBLISHED = 2
_DEFAULT_TOP_K = 10


@dataclass
class SearchItem:
    node_type: str
    node_id: int
    node_code: str
    name: str
    score: float
    match_level: str
    usage_mode: str | None = None
    lifecycle_status: int | None = None
    needs_clarify: bool = False
    not_buildable: bool = False
    topicality: int = 0
    """查询词在该资产文本里的出现次数（同分排序用，不对外暴露）"""


@dataclass
class Ambiguous:
    """歧义：多个高分候选难分，要求用 code 精确指定，不硬猜。"""

    message: str
    candidates: list[SearchItem]


@dataclass
class Rejected:
    """结构化拒答：走澄清而非硬选（坑清单第 4 条）。"""

    reason: str
    did_you_mean: list[str] = field(default_factory=list)


@dataclass
class Unavailable:
    """服务端不可用（与 `rejected` **平级但语义相反**）。

    ★ 为什么必须与 rejected 分开：MCP instructions 告诉 agent `rejected` 意为
    "不在覆盖范围"，agent 据此对用户说"本服务不覆盖该指标"。若把副库故障也塞进
    rejected，**数据库坏了**就变成了一句确信的假话 —— 比返回错误严重得多。
    """

    reason: str
    retryable: bool = True


@dataclass
class SearchResult:
    items: list[SearchItem] = field(default_factory=list)
    ambiguous: Ambiguous | None = None
    rejected: Rejected | None = None
    unavailable: Unavailable | None = None
    time_range: tuple[str, str] | None = None
    """预处理剥出的时间窗（交给下游意图组装，不参与语义匹配）"""
    keyword: str = ""


_SELECT_COLS = (
    "node_type, node_id, node_code, name, usage_mode, lifecycle_status, "
    "needs_clarify, not_buildable, embed_text"
)


def _coverage(keyword: str, name: str) -> float:
    """查询词占目标名称的比例，用于同分时的次序判定（0–1）。

    同分靠 code 字母序排是不可预测的：查"净收入"时 net_revenue(净收入(扣退款)) 与
    ndr(净收入留存(NDR)) 同为 0.86，字母序让 ndr 侥幸排前。覆盖度高者更可能是
    用户想要的那个（查询词几乎就是它的全名）。
    """
    if not keyword or not name:
        return 0.0
    return min(len(keyword) / len(name), 1.0)


def _topicality(keyword: str, row) -> int:
    """查询词在该资产文本里出现的次数 —— 同分时的主题相关度。

    通用词（"表""数""率"）会命中一大堆碰巧含它的资产，此时覆盖度只看长度比、
    短名字反而占优（dev 实测：查"表"时「日期维表」压过「选表决策指南」）。
    专门讲该主题的资产会**反复**提到这个词，用出现次数区分最直接。
    """
    if not keyword:
        return 0
    kw = keyword.lower()
    text = f"{row.name or ''} {getattr(row, 'embed_text', '') or ''}".lower()
    return text.count(kw)


def _item(row, score: float, level: str, *, topicality: int = 0) -> SearchItem:
    return SearchItem(
        node_type=row.node_type, node_id=row.node_id, node_code=row.node_code,
        name=row.name, score=score, match_level=level,
        usage_mode=row.usage_mode, lifecycle_status=row.lifecycle_status,
        needs_clarify=row.needs_clarify, not_buildable=row.not_buildable,
        topicality=topicality,
    )


async def build_search_auth(db):
    """检索期的身份。默认复用 MCP token 绑定的用户（见 tools.build_mcp_auth）。

    ★ 独立成模块级函数而非内联：REST 侧调用检索时应传自己的 auth，
    而测试也需要能替换它 —— 内联在类里会让这两件事都做不到。
    """
    from .tools import build_mcp_auth

    return await build_mcp_auth(db)


class SemanticSearch:
    """三级递进检索。向量路（第四级）待 R9 接入。"""

    @staticmethod
    async def _visible_models() -> set[int] | None:
        """当前调用者可见的模型集合。None = 不限制。

        ★ 走主库(授权数据在 MySQL), 与检索副库无关。

        ★ 失败时**不加过滤**并告警, 而非按最严处理: 主库短暂抖动若导致检索全量静默失效,
        返回的是"没搜到"(看起来像覆盖范围问题), 运维会去查检索副库而非主库 ——
        可用性代价与错误归因代价都远大于安全收益。真正的越权拦截由取数侧的
        assert_model_visible 独立兜住(那条路失败会明确报错, 不会静默放行)。
        """
        from app.core.database import async_db_session

        try:
            async with async_db_session() as db:
                auth = await build_search_auth(db)
                from .visibility import visible_model_ids

                return await visible_model_ids(auth)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"检索可见性判定失败，本次不加过滤（取数侧仍独立校验）: {e}"
            )
            return None

    async def search(
        self,
        query: str,
        *,
        node_types: list[str] | None = None,
        model_id: int | None = None,
        top_k: int = _DEFAULT_TOP_K,
        include_draft: bool = False,
        today: str | None = None,
        embedder=None,
        _disable_alias: bool = False,
    ) -> SearchResult:
        """检索。返回 [id + score]，"它是什么" 由 hydrate 读快照回答。

        `_disable_alias` 仅供评测的消融实验用（量化别名路贡献），不对外暴露。
        """
        # 预处理先做：时间窗剥离是纯计算，与副库可用性无关。
        # 即便检索降级，下游意图组装仍需要这个时间窗（缺了会退化成全表查询）。
        pre = preprocess(query, today=today)

        if not settings.SEMANTIC_RETRIEVAL_ENABLE:
            # "未启用"是配置态, 不是覆盖边界 —— 归 unavailable
            return SearchResult(
                unavailable=Unavailable(
                    reason="语义检索服务未启用，请联系管理员开启检索副库", retryable=False,
                ),
                time_range=pre.time_range, keyword=pre.keyword,
            )

        if not pre.keyword:
            return SearchResult(
                rejected=Rejected(reason="检索词为空"), time_range=pre.time_range
            )

        # ★ 元数据也要按可见性过滤: 只挡取数会泄露"存在哪些指标、口径怎么定的",
        # 而竞争性业务线之间恰恰是这类信息不该互见
        allowed_model_ids = await self._visible_models()

        try:
            best = await self._collect(
                pre, node_types, model_id, include_draft,
                disable_alias=_disable_alias, embedder=embedder,
                allowed_model_ids=allowed_model_ids,
            )
        except Exception as e:  # noqa: BLE001 - 副库故障降级, 不炸调用方
            # 异常细节只进日志: 原始文本含内网表名/主机名, 流向外部 agent 是信息泄露,
            # 且对 LLM 毫无用处(它修不了 PG, 也不该把内部拓扑复述给用户)
            logger.warning(f"语义检索失败，降级为不可用: {e}")
            return SearchResult(
                unavailable=Unavailable(reason="检索服务暂时不可用，请稍后重试"),
                time_range=pre.time_range, keyword=pre.keyword,
            )

        items = sorted(
            best.values(),
            key=lambda i: (
                -i.score,
                # coverage 是更强的信号（查询词≈全名）；topicality 只在 coverage 相近时
                # 起作用。反过来放会把「净收入」判给 ndr(净收入留存, 提了两次"净收入")
                # 而非 net_revenue —— dev 实测 P@1 会从 95% 掉到 92.5%。
                -round(_coverage(pre.keyword, i.name), 2),
                -i.topicality,
                i.node_code,
            ),
        )[:top_k]
        result = SearchResult(
            items=items, time_range=pre.time_range, keyword=pre.keyword
        )

        if not items:
            result.rejected = Rejected(
                reason=f"未找到与「{pre.keyword}」匹配的语义资产",
                did_you_mean=await self._did_you_mean(pre, node_types),
            )
            return result

        if amb := self._detect_ambiguous(items):
            result.ambiguous = amb
        return result

    # ---- 三级递进 ----

    async def _collect(
        self,
        pre,
        node_types: list[str] | None,
        model_id: int | None,
        include_draft: bool,
        *,
        disable_alias: bool = False,
        embedder=None,
        allowed_model_ids: set[int] | None = None,
    ) -> dict[tuple[str, int], SearchItem]:
        best: dict[tuple[str, int], SearchItem] = {}

        def _upsert(row, score: float, level: str) -> None:
            """同节点多路命中取最高分，不累加。"""
            key = (row.node_type, row.node_id)
            current = best.get(key)
            if current is None or score > current.score:
                best[key] = _item(
                    row, score, level, topicality=_topicality(pre.keyword, row)
                )

        # 草稿默认不召回；但 not_buildable / needs_clarify 本就是草稿态且必须可召回，
        # 否则无法做边界拒答与澄清（设计 §7.3）
        draft_clause = (
            ""
            if include_draft
            else f" AND (lifecycle_status IS NULL OR lifecycle_status = {_STATUS_PUBLISHED}"
                 " OR needs_clarify OR not_buildable)"
        )
        type_clause = " AND node_type = ANY(:types)" if node_types else ""
        model_clause = " AND :model_id = ANY(model_ids)" if model_id is not None else ""
        # 可见性过滤: 数组相交(模型间共享的节点只要有一个可见模型就该召回)。
        # allowed 为 None 表示无受限模型 → 不加条件(别给每次检索白搭一个 SQL 条件)
        visible_clause = (
            " AND model_ids && :allowed_models" if allowed_model_ids is not None else ""
        )
        common = draft_clause + type_clause + model_clause + visible_clause

        params: dict = {"kw": pre.keyword}
        if node_types:
            params["types"] = node_types
        if model_id is not None:
            params["model_id"] = model_id
        if allowed_model_ids is not None:
            # 空集合也要传: 那表示"什么都不可见", 不能退化成不过滤
            params["allowed_models"] = list(allowed_model_ids) or [-1]

        async with retrieval_db_session() as session:
            # ---- L1 精确：code → name → 别名 ----
            rows = (await session.execute(
                text(f"SELECT {_SELECT_COLS} FROM sem_retrieval_node WHERE node_code = :kw{common}"),
                params,
            )).all()
            for r in rows:
                _upsert(r, _SCORE_CODE, "exact")

            rows = (await session.execute(
                text(f"SELECT {_SELECT_COLS} FROM sem_retrieval_node WHERE lower(name) = :kw{common}"),
                params,
            )).all()
            for r in rows:
                _upsert(r, _SCORE_NAME, "exact")

            if not disable_alias:  # 消融实验可关掉别名路以量化其贡献
                rows = (await session.execute(
                    text(f"""
                        SELECT {_SELECT_COLS} FROM sem_retrieval_node n
                        WHERE EXISTS (
                          SELECT 1 FROM sem_retrieval_alias a
                          WHERE a.node_type = n.node_type AND a.node_id = n.node_id
                            AND a.alias = :alias AND NOT a.is_deprecated
                        ){common}
                    """),
                    {**params, "alias": normalize_alias(pre.keyword)},
                )).all()
                for r in rows:
                    _upsert(r, _SCORE_ALIAS, "exact")

            # ---- L2 模糊：ILIKE 子串，按字段给分（code 0.89 / name 0.86 / embed 0.75）----
            # 多 token 时要求**同一字段内全含**：允许跨字段分散命中就等于
            # "这些词出现过即可"，与全文路语义重叠，却拿了更高的模糊分。
            fuzzy_fields = (
                ("node_code", _SCORE_FUZZY_CODE),
                ("name", _SCORE_FUZZY_NAME),
                ("embed_text", _SCORE_FUZZY_EMBED),
            )
            if len(pre.tokens) > 1:
                tok_params = {f"t{i}": f"%{t}%" for i, t in enumerate(pre.tokens)}
                for column, score in fuzzy_fields:
                    same_field_and = " AND ".join(
                        f"{column} ILIKE :t{i}" for i in range(len(pre.tokens))
                    )
                    rows = (await session.execute(
                        text(f"SELECT {_SELECT_COLS} FROM sem_retrieval_node WHERE {same_field_and}{common}"),
                        {**params, **tok_params},
                    )).all()
                    for r in rows:
                        _upsert(r, score, "fuzzy")
            else:
                pattern = f"%{pre.keyword}%"
                for column, score in fuzzy_fields:
                    rows = (await session.execute(
                        text(f"SELECT {_SELECT_COLS} FROM sem_retrieval_node WHERE {column} ILIKE :pat{common}"),
                        {**params, "pat": pattern},
                    )).all()
                    for r in rows:
                        _upsert(r, score, "fuzzy")

            # ---- L3 全文：jieba 分词后 ts_rank_cd，写入侧同一个 tokenize ----
            if settings.SEM_RETRIEVAL_FTS_ENABLE:
                tokens = tokenize(pre.keyword)
                if tokens:
                    rows = (await session.execute(
                        text(f"""
                            SELECT {_SELECT_COLS},
                                   ts_rank_cd(tsv, plainto_tsquery('simple', :tok)) AS rank
                            FROM sem_retrieval_node
                            WHERE tsv @@ plainto_tsquery('simple', :tok){common}
                            ORDER BY rank DESC LIMIT 50
                        """),
                        {**params, "tok": tokens},
                    )).all()
                    for r in rows:
                        # rank 归一化到 0.50–0.69（rank 0.5 视作满分）
                        score = _FTS_BASE + min(float(r.rank) / 0.5, 1.0) * _FTS_SPAN
                        _upsert(r, round(score, 4), "fts")

            # ---- L4 向量：pgvector 余弦。前三路都是字面/分词证据，这一路补语义近似 ----
            # 判据走 vector_path_active(env 熔断 + DB 启用 + 端点已配), 与补齐期同源 ——
            # 分开判会漂移(补齐只看 DB 时会在 env 关闭下白生成向量)
            if await self._vector_active(embedder):
                await self._vector_recall(session, pre, common, params, _upsert, embedder)

        return best

    @staticmethod
    async def _vector_active(embedder) -> bool:
        """向量路是否生效。注入了假 embedder 时(测试)只看 env 熔断, 不查库配置。"""
        from .embedding import vector_path_active

        if embedder is not None:
            return bool(settings.SEM_RETRIEVAL_VECTOR_ENABLE)
        active, _ = await vector_path_active()
        return active

    @staticmethod
    async def _vector_recall(session, pre, common: str, params: dict, _upsert, embedder) -> None:
        """向量召回（第四级）。端点故障只告警 —— 前三路的结果不该被它拖掉。"""
        from .embedding import OpenAILikeEmbedder, _load_config

        try:
            if embedder is None:
                config = await _load_config()
                if not config.enabled:
                    return
                client = OpenAILikeEmbedder(config=config)
            else:
                client = embedder
            vectors = await client.embed([pre.keyword])
            if not vectors:
                return
            qvec = "[" + ",".join(str(float(v)) for v in vectors[0]) + "]"
            rows = (await session.execute(
                text(f"""
                    SELECT {_SELECT_COLS}, embedding <=> CAST(:qvec AS vector) AS dist
                    FROM sem_retrieval_node
                    WHERE embedding IS NOT NULL
                      AND embedding <=> CAST(:qvec AS vector) < :threshold{common}
                    ORDER BY dist LIMIT 20
                """),
                {**params, "qvec": qvec, "threshold": settings.SEM_RETRIEVAL_VECTOR_THRESHOLD},
            )).all()
            for r in rows:
                # 距离越小越相似：dist=0 → 0.49，dist=threshold → 0.30
                ratio = 1.0 - min(float(r.dist) / settings.SEM_RETRIEVAL_VECTOR_THRESHOLD, 1.0)
                _upsert(r, round(_VEC_BASE + ratio * _VEC_SPAN, 4), "vector")
        except Exception as e:  # noqa: BLE001 - 向量是增强，失败不影响前三路
            logger.warning(f"向量召回失败，跳过该路: {e}")

    # ---- 拒答与澄清 ----

    @staticmethod
    def _detect_ambiguous(items: list[SearchItem]) -> Ambiguous | None:
        """Top1/Top2 都 ≥0.9 且差 <0.03 → 歧义，要求用 code 精确指定。"""
        if len(items) < 2:
            return None
        first, second = items[0], items[1]
        if (
            first.score >= _AMBIGUOUS_FLOOR
            and second.score >= _AMBIGUOUS_FLOOR
            and (first.score - second.score) < _AMBIGUOUS_GAP
        ):
            tied = [i for i in items if abs(i.score - first.score) < _AMBIGUOUS_GAP]
            codes = "、".join(i.node_code for i in tied)
            return Ambiguous(
                message=f"对应多个语义资产（{codes}），请用 code 精确指定",
                candidates=tied,
            )
        return None

    async def _did_you_mean(self, pre, node_types: list[str] | None) -> list[str]:
        """零命中时给近似建议：按首个 token 做宽松子串匹配。"""
        probe = pre.tokens[0] if pre.tokens else pre.keyword
        if not probe or len(probe) < 2:
            return []
        type_clause = " AND node_type = ANY(:types)" if node_types else ""
        params: dict = {"pat": f"%{probe}%"}
        if node_types:
            params["types"] = node_types
        try:
            async with retrieval_db_session() as session:
                rows = (await session.execute(
                    text(f"""
                        SELECT node_code FROM sem_retrieval_node
                        WHERE (name ILIKE :pat OR node_code ILIKE :pat){type_clause}
                        LIMIT 5
                    """),
                    params,
                )).scalars().all()
            return list(rows)
        except Exception:  # noqa: BLE001 - 建议是附赠，失败不影响拒答本身
            return []
