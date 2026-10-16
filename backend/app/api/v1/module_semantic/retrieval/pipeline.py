"""取数编排（P4-4）：把 P3+P4 的零件串成一条链。

参考方案 §0 在线链路：
  预处理 → 检索 → hydrate → **意图** → 校验 → 填参 → 执行 → 解读
             ↑ P3 ↑              ↑ 本模块编排 P4-1/2/3 ↑

意图由 LLM 给（受 JSON Schema 约束），本模块**不**生成意图 —— 它把意图安全地变成
结果，或在任何一环拒绝时给出结构化理由让 agent 反思/澄清。

★ 结果必带口径声明（§3⑨）：没有它，"1534830.71" 就是脱离口径的裸数字，
用户无从判断可信度。

## 分流：跨实体维度走 join 编译（受控两表 JOIN 设计 §四）

判据来自快照自身：`dimensions[].bindings[].entity` 与 `indicator.primary_entity`
比对，不同即"维度不在主实体上"。此时填参路（`bind_sql`）根本拼不出正确 SQL ——
它只会把 `city` 当成主表的列插进 GROUP BY，得到"列不存在"或（更坏）绑到主表上
一个同名列。

★ **主路径不变是这条分流的硬约束**：单实体问法与无维度问法一律仍走 `bind_sql`。
dev 快照实测 20 个指标的 `available_dimensions` 里含跨实体维度的数量是 **0**
（放行它们是另一件事，见设计 §八的白名单关卡），所以本分流对现有全部问法零影响，
新增能力是纯增量。测试里有用例断言这两类问法产出的 SQL 不含 JOIN。

## 一切不确定都拒绝，不降级出数

join 路的每一条拒绝都必须在**发出任何查询之前**返回结构化错误（连 EXPLAIN 都不发）。
理由是设计 §二那组实测：经一对多边的 `SUM(final_revenue)` 从 524,147.88 变成
628,096.97（+19.8%），两条 SQL 都 `ok=True`、都过 EXPLAIN、**没有任何一层报错**。
一旦 SQL 被发出去，就再没有地方能发现问题。
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.core.logger import logger

from .agg_shape import extract_agg_expr, is_amplified_by_join
from .caliber_parts import CaliberParseError, source_table, split_where
from .dialect import sqlglot_dialect
from .executor import SqlGuardError, execute_readonly
from .join_compiler import JoinCompileError, compile_joined_sql
from .join_planner import Refusal, plan_join

# ★ `_PLACEHOLDER` 刻意复用 `sql_binder` 里那一份编译好的正则，不在本模块另写一个：
#   join 路与填参路"对占位符的认定"必须是同一套规则。这两条路之所以会漂到"主路径拒、
#   join 路放行"（见 `_placeholder_problem`），根子就是同一件事在两处各写了
#   一份判据（`caliber_parts._TIME_PLACEHOLDERS` 只认三个时间占位符）。再复制一份
#   模式串，下次 `sql_binder` 扩展占位符语法时这里会再次静默落后。
from .sql_binder import _PLACEHOLDER, BindError, _check_identifier, bind_sql
from .validator import QueryIntent, pick_primary, validate_intent

_DEFAULT_MAX_ROWS = 500

# 与 agg_shape / caliber_parts / join_planner / join_compiler 同一方言 ——
# 同一份口径 SQL 在两个模块里被解析成两种样子是最难查的一类不一致
# 能进 join 路的口径 SQL 只允许这三个子句。**白名单开在安全侧**：
# 出现任何别的子句（GROUP BY / ORDER BY / LIMIT / DISTINCT / CTE / JOIN / HAVING /
# 窗口 …）都说明 `compile_joined_sql` 没法忠实重现它，一律不进 join 路。
# 反过来写成"黑名单排除已知的坏子句"时，sqlglot 新增一个 arg、或口径用了一个我们
# 没想到的子句，就会被静默丢掉 —— 而丢子句的表现是"一个看起来正常的错数字"。
_SIMPLE_SELECT_ARGS = frozenset({"expressions", "from", "where"})


@dataclass
class QueryOutcome:
    """一次取数的完整结果（可 JSON 序列化，交给 agent）。"""

    ok: bool = False
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    executed_sql: str = ""
    truncated: bool = False
    caliber: dict = field(default_factory=dict)
    """口径声明：指标/单位/定义/模型版本 —— 解读时强制带出"""
    knowledge: list[dict] = field(default_factory=list)
    """always 档知识：解读时可引用的护栏说明"""
    errors: list[dict] = field(default_factory=list)
    top_n_applied: bool = False
    """本次是否按用户意图取了前 N 行（截取是意图，不是"结果不完整"）"""

    def to_dict(self) -> dict:
        out = {
            "ok": self.ok,
            "rows": self.rows,
            "row_count": self.row_count,
            "executed_sql": self.executed_sql,
            "truncated": self.truncated,
            "caliber": self.caliber,
            "knowledge": self.knowledge,
            "errors": self.errors,
        }
        # 只在发生 Top-N 时带出，避免给普通查询增加噪音字段
        if self.top_n_applied:
            out["top_n_applied"] = True
        return out


class QueryPipeline:
    """校验 → 填参 → 执行。上下文与连接均注入，便于测试与换实现。"""

    def __init__(
        self,
        *,
        context_loader: Callable[[], dict],
        connection,
    ) -> None:
        self._load_context = context_loader
        self._connection = connection

    async def run(
        self,
        intent_payload: dict,
        *,
        max_rows: int = _DEFAULT_MAX_ROWS,
        draft_hint: dict | None = None,
    ) -> QueryOutcome:
        """跑一次取数。任何一环拒绝都返回结构化理由，绝不带着疑问执行。

        `draft_hint` 来自检索命中项的标记（needs_clarify / not_buildable + note）。
        草稿与跨域指标不在快照里，hydrate 拿不到 —— 若不传这个提示，用户会得到
        "指标不存在"，而真相是"有，但口径待确认"或"本模型算不了它 + 出处在哪"。
        后者才是可行动的信息（EVAL Q11 / Q15）。
        """
        context = self._load_context() or {}
        intent = QueryIntent(
            indicator=intent_payload.get("indicator", ""),
            dimensions=list(intent_payload.get("dimensions") or []),
            filters=list(intent_payload.get("filters") or []),
            time=intent_payload.get("time"),
            order_by=intent_payload.get("order_by"),
            top_n=intent_payload.get("top_n"),
        )

        # ---- 校验（不通过绝不执行）----
        # data_layer 是 agent 声明的目标层, 交给粒度护栏(规则7)判定。不透传的话
        # 该护栏只能拦"口径自己写错层", 拦不住"agent 主动要求走明细层"(§4 Q9 考点)
        verdict = validate_intent(
            intent, context, data_layer=intent_payload.get("data_layer")
        )
        if not verdict.ok:
            errors = [e.to_dict() for e in verdict.errors]
            # 指标"不存在"可能只是它不在快照里（草稿/跨域）。检索已经知道真相，
            # 别让这个更有用的信息被"不存在"覆盖掉。
            if draft_hint and errors and errors[0]["rule"] == "indicator_exists":
                errors[0] = self._explain_absent_indicator(intent.indicator, draft_hint)
            return QueryOutcome(ok=False, errors=errors)

        ind = next(i for i in context["indicators"] if i["code"] == intent.indicator)
        # 按 role 选主口径, 不靠位置 —— 快照 impls 无序(编译时无 ORDER BY),
        # 取 [0] 可能拿到 secondary 并把它当权威口径执行(见 pick_primary)
        primary = pick_primary(ind.get("impls")) or {}

        # ---- Top-N 生效上限：意图截取(top_n)不得突破防扫描护栏(max_rows) ----
        # top_n 是"要前 N 名"(业务意图)，max_rows 是"别把库拉爆"(护栏)。二者都在时
        # 取 min：若 top_n 更大，实际截到护栏，且这属于"结果不完整"(truncated)，
        # 不是干净的 Top-N。
        effective_max_rows = max_rows
        top_n_capped_by_guard = False
        if intent.top_n is not None:
            if intent.top_n <= max_rows:
                effective_max_rows = intent.top_n
            else:
                top_n_capped_by_guard = True  # 想要的 N 超过护栏,只能截到护栏

        # ---- 分流：跨实体维度走 join 编译，否则填参（主路径不变）----
        cross = self._cross_entity_dimensions(intent.dimensions, ind, context)
        join_warnings: list[str] = []
        if cross:
            # 跨实体 join 的 SQL 是另一套 sqlglot 重建逻辑，排序键落在重建后的投影上
            # 语义不清；这里显式拒绝而不静默忽略（与本模块"拒绝而不猜"的一贯立场一致）。
            if intent.order_by is not None or intent.top_n is not None:
                return QueryOutcome(ok=False, errors=[{
                    "rule": "join_order_unsupported",
                    "message": (
                        "跨实体维度取数暂不支持 order_by / top_n —— 关联重建后的排序键"
                        "语义不明确，本服务不猜。请去掉排序/Top-N 先取回该维度的聚合结果，"
                        "在结果上自行排序取前 N；或改按主实体上的维度做排名。"
                    ),
                    "allowed": [
                        "去掉 order_by / top_n 后重试（仍会按该跨实体维度拆分聚合）",
                        "或改用主实体上的维度做排名（主实体路径支持 order_by + top_n）",
                    ],
                }])
            sql, join_warnings, err = self._try_compile_join(
                ind=ind, intent=intent, context=context, cross=cross, max_rows=effective_max_rows,
            )
            if err:
                # 拒绝路径：连 EXPLAIN 都还没发出去，这正是它的意义
                return QueryOutcome(ok=False, errors=[err])
        else:
            # ---- 填参（不生成 SQL）----
            try:
                sql = bind_sql(
                    primary["sql_text"],
                    time=intent.time or {},
                    group_by=self._resolve_group_columns(intent.dimensions, context),
                    filters=intent.filters,
                    order_by=self._resolve_order_by(intent.order_by, ind, context),
                )
            except BindError as e:
                return QueryOutcome(ok=False, errors=[{"rule": "bind", "message": str(e)}])

        # ---- 执行 ----
        try:
            exec_result = await execute_readonly(
                sql, connection=self._connection, max_rows=effective_max_rows
            )
        except SqlGuardError as e:
            # 权威口径本该合规；触发说明快照里的 SQL 有问题，值得告警
            logger.warning(f"权威口径 SQL 未通过只读守卫[{intent.indicator}]: {e}")
            return QueryOutcome(ok=False, errors=[{"rule": "sql_guard", "message": str(e)}])

        caliber = self._build_caliber(ind, context)
        if join_warnings:
            # ★ 这是 join 路唯一的审计留痕。一对多边只因为聚合是 COUNT(DISTINCT …)
            # 才被批准放行，结果里必须说清"经一对多关联取数"，否则用户无从判断
            # 该不该信这个数；`additivity` 疑似标错也只从这里流向治理侧。
            # 没有别的通道承载它 —— 丢掉就等于这次放行没有任何记录。
            caliber["warnings"] = join_warnings
        always_kn = [
            k for k in (context.get("knowledge") or []) if k.get("usage_mode") == "always"
        ]

        if not exec_result.ok:
            return QueryOutcome(
                ok=False, executed_sql=exec_result.executed_sql,
                caliber=caliber, knowledge=always_kn,
                errors=[{"rule": "execute", "message": exec_result.error or "执行失败"}],
            )

        # truncated 语义澄清：干净的 Top-N（top_n ≤ 护栏）取到恰好 N 行是**用户意图**，
        # 不是"结果不完整"，此时不报 truncated —— 否则 agent 会误以为该向用户报警。
        # 只有当想要的 N 超过护栏、被迫截到护栏时，truncated 才保留其"结果不完整"含义。
        clean_top_n = intent.top_n is not None and not top_n_capped_by_guard
        truncated = exec_result.truncated and not clean_top_n

        return QueryOutcome(
            ok=True, rows=exec_result.rows, row_count=exec_result.row_count,
            executed_sql=exec_result.executed_sql, truncated=truncated,
            caliber=caliber, knowledge=always_kn,
            top_n_applied=intent.top_n is not None,
        )

    # ---- 内部 ----

    @staticmethod
    def _explain_absent_indicator(code: str, hint: dict) -> dict:
        """把"指标不在快照里"翻译成用户可行动的说明。"""
        note = hint.get("note") or ""
        if hint.get("not_buildable"):
            return {
                "rule": "not_buildable",
                "message": f"指标[{code}]无法从本模型计算。{note}",
                "not_buildable": True,
            }
        if hint.get("needs_clarify"):
            return {
                "rule": "lifecycle_published",
                "message": f"指标[{code}]口径待确认，不可直接取数。{note}",
                "needs_clarify": True,
            }
        return {
            "rule": "indicator_exists",
            "message": f"指标[{code}]不在本模型的权威口径中。{note}",
        }

    @staticmethod
    def _resolve_group_columns(dimension_codes: list[str], context: dict) -> list[str]:
        """维度 code → 物理列名。

        agent 说 `dim_product_type`，SQL 里必须是 `product_type` —— 把 dim_ 前缀
        带进 SQL 会直接报"列不存在"。列名取自维度的 binding。
        """
        by_code = {d["code"]: d for d in context.get("dimensions") or []}
        columns: list[str] = []
        for code in dimension_codes:
            dim = by_code.get(code)
            if not dim:
                continue
            bindings = dim.get("bindings") or []
            if bindings and bindings[0].get("field"):
                columns.append(bindings[0]["field"])
        return columns

    def _resolve_order_by(
        self, order_by: dict | None, ind: dict, context: dict
    ) -> dict | None:
        """把 agent 给的语义排序键翻译成 SQL 里的真实标识符。

        agent 只认语义 code：指标 code(按度量值排) 或分组维度 code(按分组列排)。
        - 指标 code → 权威口径 SQL 的**度量别名**(如 `value` / `total_revenue`)，
          从 impl.sql_text 里解析,不硬编码(不同指标别名不同)。
        - 维度 code → 分组列名(复用 _resolve_group_columns 的 binding 映射)。

        校验器(规则9)已保证 field 落在"指标 code 或本次维度"之内,这里只做翻译;
        翻不出来(理论上不该发生)则返回 None,相当于不排序,绝不把生 code 拼进 SQL。
        """
        if not order_by:
            return None
        field_code = order_by.get("field")
        direction = order_by.get("direction", "desc")

        # 指标本身 → 度量别名
        if field_code == ind.get("code"):
            primary = pick_primary(ind.get("impls")) or {}
            alias = self._measure_alias(primary.get("sql_text") or "")
            if alias:
                return {"field": alias, "direction": direction}
            return None

        # 分组维度 → 物理列
        cols = self._resolve_group_columns([field_code], context)
        if cols:
            return {"field": cols[0], "direction": direction}
        return None

    @staticmethod
    def _measure_alias(sql_text: str) -> str | None:
        """从权威口径 SQL 里取度量列的输出别名(SELECT ... AS <alias>)。

        用 sqlglot 解析而非正则:口径 SQL 形如 `SELECT SUM(x) AS value FROM ...`
        或 `SELECT ROUND(...) AS gross_margin ...`,别名随指标而变。取第一个投影的
        输出名。占位符 `{start}` 不影响 SELECT 投影的解析(它们在 WHERE 里)。
        """
        if not sql_text.strip():
            return None
        try:
            tree = sqlglot.parse_one(sql_text, dialect=sqlglot_dialect())
        except Exception:  # noqa: BLE001 - 解析不了就不排序,不阻断取数
            return None
        if not isinstance(tree, exp.Select) or not tree.expressions:
            return None
        first = tree.expressions[0]
        alias = first.alias_or_name
        return alias or None

    # ---- join 路（跨实体维度）----

    @staticmethod
    def _dimension_entities(
        dimension_codes: list[str], context: dict
    ) -> dict[str, tuple[str, str]]:
        """维度 code → (所属实体 code, 物理列名)。

        快照的 `dimensions[].bindings[]` 带 `entity` 字段（见 `model/service.py`
        的 `_compile_snapshot`：`{"entity": e.code, "field": d.bind_field_code,
        "binding_type": "primary"}`），所以判"这个维度在不在主实体上"不需要额外查库。

        ★ 与 `_resolve_group_columns` 有一段重复的绑定遍历（by_code / bindings[0] /
        field 判空）。**刻意不合并**：`_resolve_group_columns` 在主路径（填参）上，
        本函数在 join 路上，两者的"主路径不得改变"约束要求前者一个字节都不动。
        真要收敛，该在 Task 6 那次改动里连同白名单一起做，届时两条路会一起被测。

        ★ 只取 `bindings[0]`，与既有 `_resolve_group_columns` 保持一致 ——
        dev 快照实测 29/29 个维度都恰好 1 条绑定（编译处硬编码成单元素列表）。
        多绑定形态一旦出现，"该用哪条"是新的口径决策，不该由本模块默默挑一条；
        真出现时会被下面的 `_cross_entity_dimensions` 当作跨实体维度送进 join 路，
        再由 `plan_join` 按它自己的规则判 —— 不会静默出数。
        """
        by_code = {d["code"]: d for d in context.get("dimensions") or []}
        out: dict[str, tuple[str, str]] = {}
        for code in dimension_codes:
            dim = by_code.get(code)
            if not dim:
                continue
            bindings = dim.get("bindings") or []
            if bindings and bindings[0].get("field"):
                out[code] = (bindings[0].get("entity") or "", bindings[0]["field"])
        return out

    @classmethod
    def _cross_entity_dimensions(
        cls, dimension_codes: list[str], ind: dict, context: dict
    ) -> dict[str, tuple[str, str]]:
        """本次请求里"不在主实体上"的维度。空 dict = 走主路径（填参）。

        实体名按 `strip().lower()` 比 —— 与 `join_planner._norm` 同一套规则。
        若这里按原样比，快照里 `primary_entity='order_revenue'` 而维度绑定写成
        `'Order_Revenue'`（都是可手改的 JSON）就会被判成跨实体，进 join 路后
        `plan_join` 又用 `_norm` 判成自关联并回 `no_join_needed` —— 一个本该走主路径
        的正常问法变成拒答。两边用同一套规则才不会串味。
        """
        # `primary_entity` 为空时 `main == ""`，于是**每个**有绑定的维度都被判成跨实体，
        # 一个本该走填参路的问法会拿到 `multi_hop_unsupported`（一个与真相无关的规则名）。
        # 这里不加守卫是因为它**不可达**：快照编译 (`model/service.py::_compile_snapshot`)
        # 按 `primary_entity_id` 非空过滤指标，且总会写上这个键。留这行注释是为了下一个
        # 读到 `multi_hop_unsupported` 的人不把那个规则名当字面意思看。
        main = (ind.get("primary_entity") or "").strip().lower()
        return {
            code: (entity, column)
            for code, (entity, column) in cls._dimension_entities(
                dimension_codes, context
            ).items()
            if entity and entity.strip().lower() != main
        }

    @staticmethod
    def _entity_table(entity_code: str, context: dict) -> str | None:
        """实体 code → `db.table`。缺物理绑定则 None。

        实体名按 `strip().lower()` 比（与上面同一套规则）：按原样比时一个大小写不一致
        的登记会让这里返回 None，而错误信息会说"实体未绑定物理表" —— 把一个大小写
        问题报成一个建模缺失，运维会去查一个不存在的问题。
        """
        want = (entity_code or "").strip().lower()
        for e in context.get("entities") or []:
            if (e.get("code") or "").strip().lower() != want:
                continue
            phys = e.get("physical") or {}
            db, tbl = phys.get("database"), phys.get("table")
            return f"{db}.{tbl}" if db and tbl else None
        return None

    @staticmethod
    def _simple_select_problem(sql_text: str) -> str | None:
        """口径 SQL 是不是"单层单表简单聚合"。返回问题描述，`None` 表示可进 join 路。

        ★ 这道闸是本次接线补上的（计划稿没有），因为**它拦的是一个 dev 上就能触发的
        静默错数**。实测 dev 的 `order_cnt` 权威口径是：

            SELECT order_type, COUNT(DISTINCT order_id) AS order_cnt
            FROM dws.dws_revenue_order_df
            WHERE order_date BETWEEN '{start}' AND '{end}'
            GROUP BY order_type

        它自带一个 `GROUP BY order_type`。而 `compile_joined_sql` 的产物只有
        "跨实体分组列 + 聚合"，那个 `order_type` 连同它的 GROUP BY **一起消失**：
        原口径按订单类型分组统计，编译产物变成"全部订单类型混在一起"。语法没错、
        EXPLAIN 通过、数字照样返回 —— 与 fan-out 虚增同一档故障。

        `split_where` / `source_table` 拦不住它：那两个函数只看 WHERE 与表，
        `HAVING` 与顶层集合运算之外的子句（GROUP BY / ORDER BY / LIMIT / DISTINCT /
        CTE / JOIN / 窗口）全在它们的视野之外。`extract_agg_expr` 也拦不住 ——
        它只数聚合个数，`order_cnt` 恰好只有一个。

        判据是**白名单**（`_SIMPLE_SELECT_ARGS`）：只允许 `expressions` / `from` /
        `where` 三个 arg，且投影恰好一列、且那一列就是聚合本身。白名单开在安全侧的
        理由与 `join_planner` 那一节相同：写成"排除已知的坏子句"时，sqlglot 升级新增
        一个 arg 或口径用了我们没想到的子句，就会静默溜进 join 路。

        ★ 终审 Important 2：白名单只看**顶层** `Select.args`，而 `from` 是被允许的
        （正常口径需要它）—— 于是 `from` 里放一张**派生表**时，那张子查询的全部语义
        都在这道闸的视野之外。实测复现：

            SELECT SUM(a) FROM (SELECT u, MAX(a) AS a FROM dws.t GROUP BY u) z
            WHERE d BETWEEN '{start}' AND '{end}'
            → _simple_select_problem 返回 None（放行）
            → source_table 返回 'dws.t' —— **内层那张原始明细表**

        编译产物于是直接对明细表做聚合：内层的 `GROUP BY u` / `MAX` 预聚合**整段消失**，
        `t0` 不再是口径所指的那个集合。这与本闸门当初要拦的 `order_cnt`（自带
        `GROUP BY order_type`）是**同一个签名**，只是分组藏进了派生表，顶层 args 看不见。
        `SELECT DISTINCT` 在派生表里复现完全一致 —— 而那正是 fan-out 那一类：
        让数字正确的那个去重被丢掉之后，直接对明细表 SUM 就回到了
        524,147.88 → 628,096.97（+19.8%）。

        `caliber_parts` 只在派生表**自带 WHERE** 时才因"2 个 WHERE"抛错，所以这个洞
        看着像已经覆盖了。故这里补一条：`from` 的目标必须是一个普通 `exp.Table`。

        设计 §六「降级」正是这一条：复合口径不进 join 路；若同时要求跨实体维度则拒绝
        （`not_decomposable`）。
        """
        try:
            statements = sqlglot.parse(sql_text or "", dialect=sqlglot_dialect())
        except Exception as exc:  # noqa: BLE001 - 解析失败是预期分支
            return f"无法解析（{exc}）"
        real = [s for s in statements if s is not None]
        if len(real) != 1:
            return f"含 {len(real)} 条语句，只支持单条"
        tree = real[0]
        if not isinstance(tree, exp.Select):
            return f"顶层不是单条 SELECT（是 {type(tree).__name__}）"
        extra = sorted(k for k, v in tree.args.items() if v and k not in _SIMPLE_SELECT_ARGS)
        if extra:
            # 点名具体子句：读到这条拒绝的人要知道该去看口径 SQL 的哪一段
            return (
                f"含 {extra} 子句，跨实体编译无法忠实重现它们"
                f"（实测 dev 的 order_cnt 自带 GROUP BY order_type，"
                f"照编不误会让它连同分组一起消失，且数字照样返回）"
            )
        # FROM 的目标必须是一张普通表：派生表/子查询的内层语义（GROUP BY / DISTINCT /
        # 预聚合）在顶层 args 里看不见，而 `source_table` 会一路穿透到内层的明细表上
        from_arg = tree.args.get("from")
        from_target = from_arg.this if from_arg is not None else None
        if from_target is not None and not isinstance(from_target, exp.Table):
            return (
                f"FROM 源是派生表/子查询（{type(from_target).__name__}），内层聚合无法"
                f"忠实重现（实测 FROM (SELECT u, MAX(a) AS a FROM dws.t GROUP BY u) 时"
                f"source_table 取到的是内层明细表 dws.t，内层的 GROUP BY/MAX 整段消失；"
                f"内层是 SELECT DISTINCT 时丢掉的正是让数字正确的那个去重）"
            )
        if len(tree.expressions) != 1:
            return f"SELECT 列表有 {len(tree.expressions)} 列，只支持单列聚合"
        proj = tree.expressions[0]
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if not isinstance(inner, exp.AggFunc):
            return f"SELECT 的唯一投影不是纯聚合：{inner.sql(dialect=sqlglot_dialect())}"
        return None

    @staticmethod
    def _placeholder_problem(*expressions: str) -> str | None:
        """**任一**表达式入参里是否残留未填的占位符。返回问题描述，`None` 表示通过。

        ★ 这道闸补的是 **join 路弱于它替代的填参路**这个失败模式 —— 本次分流最不该
        出现的那一种。`caliber_parts.split_where` 只把含 `{start}`/`{end}`/`{as_of}`
        的谓词判成时间窗（见它的 `_TIME_PLACEHOLDERS`），**别的占位符一律落进
        `caliber_conditions`**；而口径条件是**原样**交给 `compile_joined_sql` 的，
        只有 `time_conditions` 过一遍 `bind_sql`。实测复现（口径 SQL 为
        `… WHERE order_date BETWEEN '{start}' AND '{end}' AND region = '{region}'`）：

            split_where → caliber=["region = '{region}'"]
            编译产物   → `AND (t0.region = '{region}')`，ok=True
            guard_readonly 放行、EXPLAIN 通过、静默返回 0 行

        而**同一个指标在主路径上是拒的**：`bind_sql` 抛
        `BindError: 占位符缺少取值: ['region']`（两侧均已实测比对）。一条 `{region}`
        字面量进到数据库，最好的结果是 0 行、最坏的结果是匹配到一个真叫这个名字的值 ——
        两种都不报错。

        ★ 终审 Important 1：上一版只扫 `caliber_conditions`，`agg_expr`（来自
        `measure_expression` 或 `extract_agg_expr` 反解）**从没被任何一层查过**，
        `join_compiler._qualify_strict` 当时也不查占位符。实测复现：

            measure_expression = "SUM(IF(region = '{region}', final_revenue, 0))"
            → SUM(CASE WHEN t0.region = '{region}' THEN t0.final_revenue ELSE 0 END)
            → ok=True，那个字面量直接进库

        而**同一个指标的填参路仍是拒的**（`rule=bind, 占位符缺少取值: ['region']`）——
        与口径条件那一处是同一个洞的另一半。`{start}` 落在聚合里（月累计口径的常见
        写法）更坏：CASE 恒假、**静默返回一个 0**，连"0 行"这条线索都没有。

        故本函数改成收**全部**表达式入参（`agg_expr` / `group_column` /
        `caliber_conditions` / 时间条件的**填参前**原文），而不是只收口径条件 ——
        调用点是"编译器的四个表达式入参"这个集合本身，第五个入参加进来时
        跟着传进来即可，不需要再造一道闸。`join_compiler._qualify_strict` 里另有一道
        同判据的纵深防御（那里是编译器的公开入口，不能只在"调用方先查过"时安全）。

        ★ 调用点必须排在时间条件填参（⑧）**之后**，传的是**填参后**的字符串：
        `time_conditions` 的原文里本来就有 `{start}`（那正是它被判成时间窗的原因），
        在填参前查会把每一个正常口径都误拒 —— 本轮实测踩过这一脚（dev 形态的
        `sub_revenue` 立刻被判成"有占位符"）。判据只能是"**真正交给
        `compile_joined_sql` 的那个字符串**里有没有占位符"。

        判据复用 `sql_binder._PLACEHOLDER`，理由见 import 处的注释：join 路与填参路
        对"什么算占位符"必须共用同一份定义，否则会再漂一次。
        """
        leftover = sorted({
            name
            for expr in expressions
            for name in _PLACEHOLDER.findall(expr or "")
        })
        if not leftover:
            return None
        return f"占位符缺少取值: {leftover}"

    def _try_compile_join(
        self, *, ind: dict, intent: QueryIntent, context: dict,
        cross: dict[str, tuple[str, str]], max_rows: int,
    ) -> tuple[str | None, list[str], dict | None]:
        """跨实体维度 → 编译两表 SQL。

        返回 `(sql, warnings, error)`：`error` 非空即拒绝。**拒绝时一条查询都不发**
        （连 EXPLAIN 都不发），调用方直接返回 `QueryOutcome(ok=False)`。

        ★ 只支持**一个**维度、且它必须是那个跨实体维度：编译器只产出一个分组列。
        """
        code = ind.get("code") or ""
        main_entity = ind.get("primary_entity") or ""

        # ---- ① 维度数量：编译器只有一个分组列的位置 ----
        # 两个分属不同实体的维度要两跳（fan-out 判定复杂度随跳数爆炸，每一跳都可能放大）
        if len(cross) > 1:
            return None, [], {
                "rule": "multi_hop_unsupported",
                "message": (
                    f"维度 {sorted(cross)} 分属多个实体（"
                    f"{sorted({e for e, _ in cross.values()})}），需要多跳关联，"
                    f"本功能只支持一跳"
                ),
                "allowed": [f"一次只按 {c} 拆分" for c in sorted(cross)],
            }
        # 主实体维度 + 跨实体维度同时点：旧分流会**只按跨实体那一个**分组，把主实体
        # 维度静默丢掉 —— 用户拿到一张聚合过头的表，而每个数字看起来都正常。
        others = [d for d in intent.dimensions if d not in cross]
        if others:
            return None, [], {
                "rule": "join_multi_dimension_unsupported",
                "message": (
                    f"维度 {sorted(cross)} 在实体[{next(iter(cross.values()))[0]}]上"
                    f"（不在主实体[{main_entity}]上），跨实体下钻一次只支持一个维度，"
                    f"但本次还点了 {others} —— 同时编译会让后者连同它的分组静默消失"
                ),
                "allowed": [
                    f"只按 {sorted(cross)[0]} 拆分",
                    f"或只按 {others} 拆分（这些维度在主实体上，走填参路）",
                ],
            }

        # ---- ② 附加过滤：编译器没有 filters 入参 ----
        # 旧分流会把它们**整批丢掉**：用户要"限 iOS 的订阅收入按城市"，拿到的是全平台
        # 数字且 ok=True。补前缀（t0 还是 t1）是业务判断 —— 猜错不报错，只是数字变了，
        # 与 join_compiler 拒绝给 ON 里的裸列补前缀是同一条立场。
        if intent.filters:
            return None, [], {
                "rule": "join_filter_unsupported",
                "message": (
                    f"跨实体下钻暂不支持附加过滤（本次带了 "
                    f"{[f.get('field') for f in intent.filters]}）—— 过滤该落在主表还是"
                    f"关联表是业务判断，猜错不会报错、只会让数字变了，故拒绝而不猜"
                ),
                "allowed": [
                    f"去掉过滤条件，只按 {sorted(cross)[0]} 拆分",
                    "或改按主实体上的维度拆分（填参路支持过滤）",
                ],
            }

        dim_code, (target_entity, group_column) = next(iter(cross.items()))

        # ---- ②b 分组列必须是合法标识符（与主路径同一道闸）----
        # `bindings[].field` 同样来自可手改的快照 JSON。实测未设防时：
        # `field='1'` → `GROUP BY 1`（常量分组，一行、数字错、不报错）、
        # `field='*'` → `GROUP BY *`、`field='{city}'` → `GROUP BY STRUCT(t1.city)`。
        # 主路径对这三个都拒（`bind_sql._check_identifier`：分组列必须是合法标识符），
        # 所以这是又一处"join 路比它替换的主路径更弱" —— 复用同一个检查而非另写一份。
        try:
            _check_identifier(group_column, "分组列")
        except BindError as e:
            return None, [], {"rule": "bind", "message": str(e)}

        primary = pick_primary(ind.get("impls")) or {}
        sql_text = primary.get("sql_text") or ""

        # ---- ③ 口径必须是单层单表简单聚合（设计 §六「降级」）----
        if problem := self._simple_select_problem(sql_text):
            return None, [], {
                "rule": "not_decomposable",
                "message": (
                    f"指标[{code}]的权威口径不是单层单表简单聚合（{problem}），"
                    f"不支持跨实体下钻 —— 强行编译会静默丢掉口径里的成分"
                ),
                "allowed": [
                    f"改按主实体[{main_entity}]上的维度拆分（走填参路，口径原样执行）",
                    f"或请数据负责人在 {main_entity} 上补一条按 {dim_code} 拆分的口径",
                ],
            }

        # ---- ④ 聚合形态：优先 measure_expression，为空时从口径 SQL 反解 ----
        # dev 实测 6/20 指标 `measure_expression` 为空，此时只能反解。
        # ★ 拿到之后**不再改写它**：一对多边的放行完全建立在"去重语义原样到达 SQL"上。
        declared_agg = (ind.get("measure_expression") or "").strip()
        agg_expr = declared_agg or extract_agg_expr(sql_text)
        if not agg_expr:
            return None, [], {
                "rule": "not_decomposable",
                "message": (
                    f"指标[{code}]的口径无法解析出单一聚合（measure_expression 为空且"
                    f"口径 SQL 里不是恰好一个聚合），不支持跨实体下钻"
                ),
                "allowed": [
                    f"改按主实体[{main_entity}]上的维度拆分",
                    f"或请数据负责人补全 {code} 的 measure_expression",
                ],
            }

        # ---- ④b 聚合形态必须可判定：`measure_expression` 不能免检 ----
        # ★ 上面那个 `or` 让 `measure_expression` **优先**，而 `_simple_select_problem`
        #   校验的是**口径 SQL 的投影** —— 两者不是同一个字符串。于是"真正编译进 SQL 的
        #   那个聚合"从没被任何一层看过。实测复现：`measure_expression="final_revenue"`
        #   （压根不是聚合）配一条投影为 `SUM(final_revenue)` 的合法口径，旧行为产出
        #
        #       SELECT t1.city AS city, final_revenue AS sub_revenue … GROUP BY t1.city
        #
        #   ok=True、无警告 —— 一个"随便取某一行的值"冒充聚合结果的数字。
        #   `measure_expression` 与 `on_expr`/`relationship` 出自同一份可手改的快照 JSON，
        #   那两个字段都被 `join_planner` 明确怀疑了，它没有理由拿豁免。
        #
        # 判据借 `is_amplified_by_join`：它返回 `None` 的含义正是"解析不出唯一收敛聚合"
        # （无聚合 / 多聚合 / 窗口聚合 / 多语句 / 解析失败），一次覆盖两个洞。
        # 下面 plan_join 里也会调它，但那里只在**不安全边**上调 —— many_to_one 这类安全边
        # 压根不查聚合形态，所以这道闸必须独立开在这里。
        #
        # ★ 残余风险如实记录：这**判不出**"measure_expression 与口径 SQL 算的不是同一件
        #   事"。`measure_expression="SUM(gross_revenue)"` 而口径 SQL 求和 `final_revenue`
        #   时，两者都是合法的单一聚合，本闸放行，编译产物是 `SUM(t0.gross_revenue)`
        #   而主路径执行的是 `SUM(final_revenue)` —— 两个数字都"对"，只是口径不同。
        #   这里**刻意不做交叉比对**：哪一个才是权威口径是口径判断，编译器没有任何依据
        #   下这个结论（真有指标的 measure_expression 就是故意与 SQL 投影不同的形态）。
        #   治住它的正路是治理侧保证这两个字段一致，不是让编译器猜。
        if is_amplified_by_join(agg_expr) is None:
            # 点名它来自哪里：读拒绝理由的人要知道该去改快照的 measure_expression 字段，
            # 还是该去改口径 SQL —— 两个位置由不同的人维护
            origin = "measure_expression" if declared_agg else "口径 SQL 反解出的聚合"
            return None, [], {
                "rule": "not_decomposable",
                "message": (
                    f"指标[{code}]的聚合表达式[{agg_expr}]（来自 {origin}）"
                    f"不是可判定的单一聚合"
                    f"（无聚合/多个聚合/窗口聚合），不支持跨实体下钻 —— "
                    f"它会被原样编译进 SQL（形如 SELECT {agg_expr} AS {code}），"
                    f"一个非聚合表达式配 GROUP BY 会取到某一行的值冒充聚合结果，"
                    f"且 SQL 与 EXPLAIN 都不报错"
                ),
                "allowed": [
                    f"改按主实体[{main_entity}]上的维度拆分（走填参路，口径原样执行）",
                    f"或请数据负责人把 {code} 的 measure_expression 改成单一聚合"
                    f"（形如 SUM(<列>) / COUNT(DISTINCT <列>)），或清空该字段改由口径 SQL 反解",
                ],
            }

        # ---- ⑤ 关联实体必须有物理表 ----
        # 放在 plan_join 之前：先报"这张表压根没绑"比先报一个 fan-out 理由更贴近真相
        join_table = self._entity_table(target_entity, context)
        if not join_table:
            return None, [], {
                "rule": "entity_physical_missing",
                "message": (
                    f"维度[{dim_code}]所在实体[{target_entity}]未绑定物理表，无法关联"
                ),
                "allowed": [
                    f"请数据负责人给实体 {target_entity} 绑定物理表（sem_entity.physical_table_id）",
                ],
            }

        # ---- ⑥ JOIN 可行性 + fan-out 拦截（Task 3）----
        # ★ `plan_join` 也要包起来，与 `CaliberParseError` / `JoinCompileError` 同等对待：
        #   它逐个 `j.get(...)` 读 `joins[]`，而快照是可手改的 JSON —— 实测
        #   `joins: ["garbage"]` 抛 `AttributeError: 'str' object has no attribute 'get'`
        #   并**逃出 `run()`**，agent 拿到 500，既读不到理由也无法反思。既然
        #   `relationship` / `on_expr` / `join_type` 的取值都被当成不可信，元素的**类型**
        #   没有理由例外。捕 `Exception` 而非只捕 `AttributeError`：这里防的是"快照坏了"
        #   这一整类，逐个枚举异常类型等于假设自己已经想全了坏法。
        try:
            plan = plan_join(
                main_entity=main_entity, target_entity=target_entity,
                joins=context.get("joins") or [], agg_expr=agg_expr,
                additivity=ind.get("additivity"),
            )
        except Exception as e:  # noqa: BLE001 - 快照坏了是预期分支，不能变成 500
            logger.warning(f"跨实体 join 计划失败[{code} × {dim_code}]: {e!r}")
            return None, [], {
                "rule": "join_plan_failed",
                "message": (
                    f"指标[{code}]按维度[{dim_code}]的 JOIN 关系无法判定: {e}"
                    f"（语义模型的 joins 登记形态异常，无法确认关联安全性，故拒绝而不猜）"
                ),
                "allowed": [
                    f"改按主实体[{main_entity}]上的维度拆分",
                    f"或请数据负责人核对 {main_entity} ↔ {target_entity} 的 JOIN 关系登记",
                ],
            }
        if isinstance(plan, Refusal):
            return None, [], {
                "rule": plan.rule, "message": plan.message, "allowed": plan.allowed,
            }

        # ---- ⑦ 口径条件反解 —— 失败必须拒绝，不能少条件 ----
        # ★ 两侧的表名出自**两个不同的权威**（终审 Minor 1，如实记录、行为刻意不改）：
        #   `main_table` 来自 `source_table(sql_text)`（口径 SQL 自己 FROM 的那张表），
        #   `join_table` 来自实体的 `physical`（上面 ⑤）。实测一个口径读
        #   `ads.ads_revenue_daily` 而 `primary_entity` 映射到 `dws.dws_revenue_order_df`
        #   的指标能顺利编译，产出"ADS 预聚合表 JOIN DIM 表、而 ON 里的键登记在 DWS 表上"。
        #
        #   **口径的表赢**是保守侧的正确选择：`impl.sql_text` 才是被评审过的权威口径，
        #   实体 `physical` 只是建模侧的一条登记。改成用实体表会让编译产物执行一条
        #   **没人评审过**的口径（同样的聚合作用在另一张表上，数字照样返回）——
        #   那比现在的不对称危险得多。所以这里不改行为，只在下面把不一致**留痕**：
        #   否则复核 SQL 的人看到 `FROM ads.…` 只会以为实体本来就绑在那张表上。
        try:
            caliber_conditions, time_conditions = split_where(sql_text)
            main_table = source_table(sql_text)
        except CaliberParseError as e:
            return None, [], {
                "rule": "caliber_parse_failed",
                "message": (
                    f"指标[{code}]的口径条件无法解析，不支持跨实体下钻: {e}"
                    f"（少一条口径条件会让订阅收入静默变成全部收入，"
                    f"而数字照样返回，故一律拒绝而不做降级）"
                ),
                "allowed": [f"改按主实体[{main_entity}]上的维度拆分"],
            }

        # ★ 口径里必须有可重填的时间谓词。校验器（规则 4）已经要求了时间窗，但如果
        #   口径 SQL 里没有时间谓词，编译产物就是一条**无时间过滤的两表 join** ——
        #   既是全历史的错数字，又是一次全表扫描（正是校验规则 4 要防的 B1）。
        #   填参路不会有这个问题：它不加时间条件，原口径也不带，行为一致；
        #   join 路则是我们自己重建 WHERE，缺了就真的缺了。
        if not time_conditions:
            return None, [], {
                "rule": "caliber_parse_failed",
                "message": (
                    f"指标[{code}]的权威口径里没有可重填的时间谓词"
                    f"（未出现 {{start}}/{{end}}/{{as_of}} 占位符），"
                    f"跨实体编译会产出一条无时间过滤的关联查询 —— 全历史汇总 + 全表扫描"
                ),
                "allowed": [
                    f"改按主实体[{main_entity}]上的维度拆分（填参路按原口径执行）",
                    f"或请数据负责人在 {code} 的口径里补上时间窗占位符",
                ],
            }

        # ---- ⑧ 时间条件：把原口径的时间谓词按请求的时间窗重新填参 ----
        # 借 `bind_sql` 而不是自己拼值：注入面（时间值必须匹配日期/时间戳格式、
        # `{as_of}` 缺失时回退 `end`）已经在那里收敛过一遍，这里再写一份就是两套规则。
        # ★ 直接把条件文本交给它、**不套 `SELECT 1 WHERE` 再切回来**：计划稿那样写要靠
        #   `.split("WHERE", 1)[1]` 取回结果，而 `bind_sql` 只做占位符替换（不传
        #   group_by/filters 时连改写都不做），套壳与切分是两个可以出错的多余步骤。
        try:
            time_condition = bind_sql(
                " AND ".join(time_conditions), time=intent.time or {},
            )
        except BindError as e:
            # dev 实测 revenue_mtd 的占位符是 {month_start}/{as_of}，填不上 ——
            # 必须拒绝而不是"把时间条件丢掉"，后者等于全历史扫描
            return None, [], {
                "rule": "bind",
                "message": (
                    f"指标[{code}]的时间条件无法按请求的时间窗重填: {e}"
                    f"（跨实体下钻要重建 WHERE，填不上就只能拒绝 —— "
                    f"丢掉时间条件等于全历史汇总）"
                ),
            }

        # ---- ⑧b 交给编译器的表达式里一个占位符都不许残留 ----
        # rule 沿用主路径的 `bind` —— 同一件事（"这个占位符没人给得出值"）在两条路上
        # 给同一个名字，agent 不必学两套；主路径对同一指标抛的正是
        # `BindError: 占位符缺少取值: ['region']`，本分支的 message 也照这个措辞。
        #
        # ★ 终审 Important 1：这道闸收的是**编译器的全部四个表达式入参**，不再只是
        #   口径条件。上一版漏了 `agg_expr`（它来自 `measure_expression` 或口径 SQL
        #   反解，而 `join_compiler._qualify_strict` 当时也不查占位符），实测
        #   `measure_expression="SUM(IF(region = '{region}', final_revenue, 0))"` 编译成
        #   `SUM(CASE WHEN t0.region = '{region}' THEN t0.final_revenue ELSE 0 END)`
        #   并 ok=True —— 而同一指标在填参路上抛 `占位符缺少取值: ['region']`。
        #
        # ★ 位置刻意排在 ⑧ **之后**：`time_conditions` 的原文里本来就有 `{start}`
        #   （那正是它被判成时间窗的原因），在 ⑧ 之前查会把每一个正常口径都拒掉
        #   （实测：本轮先写在 ⑦ 后面，dev 形态的 sub_revenue 立刻被误拒）。判据只能是
        #   "**真正交给 compile_joined_sql 的那个字符串**里有没有占位符" ——
        #   所以参数表与下面 ⑩ 的调用逐项对应，第五个表达式入参加进来时跟着加一项即可。
        if problem := self._placeholder_problem(
            agg_expr, group_column, time_condition, *caliber_conditions,
        ):
            return None, [], {
                "rule": "bind",
                "message": (
                    f"指标[{code}]交给跨实体编译器的表达式里有无法填充的占位符"
                    f"（{problem}）—— 跨实体编译会把它**原样**拼进 SQL"
                    f"（实测口径条件产出 AND (t0.region = '{{region}}')，ok=True 且静默"
                    f"返回 0 行；落在聚合里则产出 SUM(CASE WHEN t0.region = '{{region}}' "
                    f"THEN … END)，静默返回一个 0）；"
                    f"填参路对同一指标抛的是同一个错，join 路不得比它更宽松"
                ),
                "allowed": [
                    f"改按主实体[{main_entity}]上的维度拆分（填参路会给出同样的拒绝理由）",
                    f"或请数据负责人把 {code} 的口径 SQL / measure_expression 里的"
                    f"非时间占位符改成固定取值",
                ],
            }

        # ---- ⑨ 两个输出列的别名不能撞 ----
        # `executor.query` 用 `dict(row)` 收结果（`executor.py:193`），实测两列同名时
        # **后一列直接吃掉前一列**：`SELECT 1 AS x, 2 AS x` → `{'x': 2}`。撞名时分组标签
        # 会整列消失，用户拿到 N 行一模一样的指标值、不知道每行是哪一组，而 SQL 与
        # EXPLAIN 都不报错。dev 快照实测 0 例（29 个维度去掉 dim_ 前缀后无一撞上 20 个
        # 指标 code），但两个 code 来自不同的人工命名空间，撞名只是时间问题。
        group_alias = dim_code.removeprefix("dim_")
        if group_alias.strip().lower() == code.strip().lower():
            return None, [], {
                "rule": "join_alias_conflict",
                "message": (
                    f"维度[{dim_code}]的输出别名与指标 code[{code}]相同，"
                    f"两列同名时结果集里分组标签会被指标值整列覆盖（实测 "
                    f"SELECT 1 AS x, 2 AS x 得到 {{'x': 2}}），且不报错"
                ),
                "allowed": [
                    f"请数据负责人改掉维度 {dim_code} 或指标 {code} 的 code，使二者不同名",
                ],
            }

        # ---- ⑩ 编译（Task 4）----
        # `JoinCompileError` 必须在这里捕获：让它逃出去 agent 得到的是 500，
        # 既读不到理由也无法反思。与 CaliberParseError 同等对待。
        try:
            sql = compile_joined_sql(
                agg_expr=agg_expr, agg_alias=code,
                main_entity=main_entity, main_table=main_table, join_table=join_table,
                plan=plan, group_column=group_column, group_alias=group_alias,
                caliber_conditions=caliber_conditions, time_condition=time_condition,
                limit=max_rows,
            )
        except JoinCompileError as e:
            logger.warning(f"跨实体 join 编译失败[{code} × {dim_code}]: {e}")
            return None, [], {
                "rule": "join_compile_failed",
                "message": (
                    f"指标[{code}]按维度[{dim_code}]的跨实体关联无法编译: {e}"
                ),
                "allowed": [
                    f"改按主实体[{main_entity}]上的维度拆分",
                    "或请数据负责人核对该 JOIN 关系的 on_expr 登记",
                ],
            }

        # ---- ⑪ 留痕：两处还能改变数字、但不足以拒绝的事 ----
        # 走到这里 SQL 已经编成了，两条都不是拒绝理由（拒了会误伤合法形态），
        # 但都会让数字与"单表口径直接执行"不同 —— `caliber.warnings` 是它们唯一的出口。
        warnings = list(plan.warnings)

        # ★ 终审 Minor 2：`INNER JOIN` 会**丢掉主表里没有维度匹配的行**。在
        #   `many_to_one` 边上这不放大、只**缩小**：SUM 静默变小，ok=True。
        #   `join_planner` 自己论证过"静默变小与虚增一样不可核对"，但它只把 join_type
        #   白名单收敛到 {inner,left} 就收手了 —— 实测把 dim_user 那条边改成 inner，
        #   产出 `INNER JOIN` 且**一条警告都没有**。这是最后一个还能改变数字的杠杆。
        #   刻意**不拒**：inner 是 `sem_join` 的合法登记，且在"维表保证全覆盖"的
        #   建模下它与 left 等价 —— 判不了就留痕，不替治理侧下结论。
        if (plan.join_type or "").strip().upper() == "INNER":
            warnings.append(
                f"本次按 INNER JOIN 关联[{main_entity}→{target_entity}]"
                f"（该边在语义模型里登记为 inner）：主表中没有匹配到"
                f"[{target_entity}]的行会被**排除**，故合计值可能比单表口径的数字**偏小**，"
                f"差额即无匹配维度的部分；如需保留这些行请让数据负责人把该边改为 left"
            )

        # ★ 终审 Minor 1：主表来自口径 SQL、join 表来自实体 physical —— 两个权威。
        #   不一致时行为不变（口径的表赢，理由见 ⑦ 处的注释），但必须可见：
        #   实测一个读 `ads.ads_revenue_daily` 的口径配 `physical=dws.dws_revenue_order_df`
        #   的实体能顺利编译，产出"ADS 预聚合表 JOIN DIM 表、键登记在 DWS 表上"，
        #   而复核 SQL 的人看到 `FROM ads.…` 只会以为实体就绑在那张表上。
        entity_table = self._entity_table(main_entity, context)
        if entity_table and entity_table.strip().lower() != (
            main_table or ""
        ).strip().lower():
            warnings.append(
                f"主表取自权威口径 SQL 的 FROM（{main_table}），与主实体"
                f"[{main_entity}]登记的物理表（{entity_table}）不是同一张；"
                f"本次按口径 SQL 的表编译（口径才是被评审过的权威），"
                f"但 ON 条件里的关联键是登记在 {entity_table} 上的 —— "
                f"请数据负责人核对该指标口径与实体物理绑定是否应当一致"
            )

        return sql, warnings, None

    @staticmethod
    def _build_caliber(ind: dict, context: dict) -> dict:
        """口径声明。解读时强制带出，否则结果是脱离口径的裸数字。

        ★ data_layer/granularity 必须取**实际执行的那个**口径（按 role 选），
        否则声明与执行不符 —— 用户看到"DWS/day"而实际跑的是 DWD 明细，
        比不给声明更糟（它提供了虚假的可信度）。
        """
        primary = pick_primary(ind.get("impls")) or {}
        return {
            "indicator": f"{ind.get('name') or ''}({ind['code']})",
            "unit": ind.get("unit"),
            "definition": ind.get("business_definition") or ind.get("description") or "",
            "data_layer": primary.get("data_layer"),
            "granularity": primary.get("granularity"),
            "model_code": context.get("model_code"),
            "model_version": context.get("version"),
        }
