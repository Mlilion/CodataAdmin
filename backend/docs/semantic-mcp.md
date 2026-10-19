# 语义 MCP 接入说明（给 Codata 侧）

CodataAdmin 作为 **MCP server**，把语义模型的检索、权威口径召回与取数能力暴露给外部 agent。

```
Codata (agent, MCP client) ──Streamable HTTP──▶ CodataAdmin (MCP server)
                                                 ├─ list_models       发现 model_id
                                                 ├─ search_semantic   找到哪个
                                                 ├─ get_model_context 召回权威口径
                                                 ├─ query_indicator   取数（权威口径）
                                                 └─ execute_sql       裸 SQL（探索用，无口径背书）
```

## 一、连接

| 项 | 值 |
|---|---|
| 端点 | `http://<host>:<port>/mcp`（`/mcp/` 亦可） |
| 传输 | Streamable HTTP，`stateless_http=True`（多 worker 安全） |
| 鉴权 | `Authorization: Bearer <token>`（见 §四「MCP 鉴权」，支持多 token） |
| 方法 | GET / POST / DELETE |

★ **不用照着上面的占位符猜地址**：登录管理端 → 语义中心 →「MCP 令牌」，
页面顶部直接给出**这套部署的真实地址**，可一键复制；签发令牌的弹窗里
地址与令牌一起给全（那是唯一能看到明文的时刻）。页面同时显示该端点
**此刻是否真在服务** —— 未挂载时会说明原因，而不是给一个 404 的地址。

★ `/mcp` 挂在**根路径**上，**不带** `/api/v1` 前缀 —— MCP 是另一套协议。
（实测直连时 `/api/v1/mcp` 也能连上：Starlette 会先剥掉 `root_path` 再匹配路由。
但那是巧合而非契约 —— 反代改写路径时就不成立了，请用不带前缀的形式。）

服务端配置：

```ini
SEM_MCP_ENABLE = True
# 对外地址。留空则由请求推导(直连部署已经对了);
# 反代/网关后面必须配 —— 那时请求里的 Host 是内网地址或容器名, 抄出去连不上。
# 写站点根地址即可, /mcp 会自动补。
SEM_MCP_PUBLIC_URL = ""
```

令牌**首选在页面上签发**：语义中心 →「MCP 令牌」→ 签发（权限 `module_semantic:mcp:token`）。
可轮换、可单独吊销、能看到每个令牌"最后何时被用过"。明文只在签发/轮换时显示一次
（库里存不可逆哈希，库泄露也拿不到可用令牌）。

env 两项**仍然有效**，作只读兜底（页面上无法轮换/吊销，改它们要重启服务）：

```ini
SEM_MCP_TOKEN = "<强随机串>"                       # 单 token（调用方记作 default）
SEM_MCP_TOKENS = "codata:<串1>:<用户名>,ops:<串2>"  # name:token[:username]
```

**库与 env 都没有任何令牌时启动会打警告**，非 dev 环境直接拒绝挂载 `/mcp`。
语义口径与数仓查询能力不该匿名开放。

★ 吊销最后一个令牌**不会**退化成无鉴权 —— 判据是"这套部署配过鉴权没有"（含已吊销
/已过期的行），而不是"当前有几个可用令牌"。否则一个安全动作会导致裸奔。

FastMCP client 示例：

```python
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

transport = StreamableHttpTransport(
    "http://codata-admin:8001/mcp",
    headers={"Authorization": "Bearer <SEM_MCP_TOKEN>"},
)
async with Client(transport) as client:
    tools = await client.list_tools()          # 5 个工具
    result = await client.call_tool("search_semantic", {"query": "6月订阅收入"})
```

## 二、主链路四个工具的调用顺序

**必须按这个顺序**，跳步会拿到错结果：

```
⓪ list_models()                   → 拿到 model_id（其余工具都要求它必填，不要猜）
① search_semantic(query)          → 拿到指标 code + 时间窗 + 标记
② get_model_context(model_id, …)  → 拿到权威口径 SQL + 可用维度白名单 + 口径知识
③ query_indicator(…)              → 取数（系统填参执行，你不写 SQL）
```

第五个工具 `execute_sql` **不在这条链路上**，是探索用的旁路，见 §二·⑤。
报业务数字一律走上面这条链路。

### ① search_semantic

不要凭记忆猜指标 code。返回里有两个标记必须处理：

| 标记 | 含义 | 该怎么做 |
|---|---|---|
| `needs_clarify=true` | 口径待确认（如续订率分子含不含 resubscribe） | **先向用户澄清**，不要自行决定 |
| `not_buildable=true` | 本模型算不了它（如 ARPU 分母是活跃用户） | 如实说明原因与正确出处，**不要用别的指标凑近似值** |

还有 `ambiguous`（多个候选难分 → 让用户用 code 指定）和 `rejected`（零命中 → 附 `did_you_mean`）。

`time_range` 是从问题里剥出的时间窗，取数时直接用。

**搜不到时**：`items` 为空是**有意义的答案**，不是工具故障。
- `rejected` 非空 → **不在本模型覆盖范围**。如实说明本服务覆盖什么、该去哪查。
  `did_you_mean` 只是**字面相近**（问 `arpu` 会建议 `arppu`，两者分母是活跃用户 vs
  付费用户），改用前必须 `get_model_context` 核对口径。**绝不用相近指标凑数** ——
  数字看起来合理、用户无从察觉，比说"没有"糟糕得多。
- `unavailable` 非空 → **服务端故障或未启用**，与 `rejected` 语义相反。
  告知"检索服务暂时不可用"，按 `retryable` 决定是否建议稍后重试。
  **绝不要**说"本服务不覆盖该指标" —— 那是假话。

### ② get_model_context

**口径 SQL 在这里，不要自己编。** 关键返回：

- `indicators[].impls[].sql_text` — 权威口径（带 `{start}`/`{end}` 占位符）
- `indicators[].available_dimensions` — **可用维度白名单**，超出会被拒
- `indicators[].business_definition` — 含基线数值与口径警告，解读时要引用
- `entities[].fields[].enum_values` — 过滤值只能用这些
- `knowledge[]` — **必须遵守并告知用户的口径说明**，尤其 `caveat` 类

### ③ query_indicator

系统取权威 SQL 填参 + 只读执行（自动加 LIMIT、先 EXPLAIN）。

把 ① 返回的 `needs_clarify` / `not_buildable` / 说明文本**原样传进来** —— 草稿与跨域指标不在权威快照里，不带标记只会得到笼统的"指标不存在"，带了才能拿到「口径待确认」或「本模型不可算 + 出处」这类可行动的说明。

`ok=true` 时必须带出 `caliber`（口径声明：指标全名/单位/口径定义/数据层/模型版本）—— 脱离口径的裸数字用户无法判断可信度。`truncated=true` 要说明结果不完整。

`ok=false` 时看 `errors[].rule`：

| rule | 处理 |
|---|---|
| `dimension_allowed` / `filter_enum` | 按 `allowed` 改正后重试 |
| `time_required` | 问用户要时间窗，不要默认查全部 |
| `lifecycle_published`（带 `needs_clarify`） | 向用户澄清口径 |
| `caliber_break` | **时间窗跨越口径分水岭、数据不可比 —— 如实告知，不要给一个看似正确的数字** |
| `not_buildable` | 说明算不了及正确出处 |
| `not_configured`（`retryable=false`） | **服务端未配数仓连接**。改参数重试无用，如实告知是平台侧问题 |
| `model_not_released`（`retryable=false`） | **该模型尚未发布**。不要改指标 code 重试 —— 猜多少次都不会对 |

### 跨实体维度下钻（2026-08-04）

`dimensions` 里的维度**可以不在指标主实体上**。系统会按 `sem_join` 里已登记的
**恰好一条**实体关系自动关联一跳、重建 SQL，口径条件（`order_type='subscription'`
这类）原样保留。设计见
`docs/superpowers/specs/2026-08-04-controlled-join-compile-design.md`。

★★ **前提（治理侧动作，不做则这个能力等于没上）**：该维度必须已在指标的
**可用维度**白名单里（`available_dimensions`），且模型已**重新发布**。
这个字段是纯人工维护的，没有任何自动填充 —— dev 实测 20/20 个指标的白名单里
跨实体维度数 = 0。步骤见
`backend/sql/patches/2026-08-04-semantic-cross-entity-dimension-note.sql`
与部署清单第 7b 步。

★ **别按"某个维度可用"来预期，要按（指标 × 维度）组合看**。dev 上绕过白名单穷举
20 指标 × 全部跨实体维度 = **350 组**：215 组成功编译，**135 组被拒**
（`not_decomposable` 85、`fanout_refused` 20、`multi_hop_unsupported` 16、`bind` 14）。
拒绝面主要来自指标侧的**口径形态**（比值类指标、口径自带 `GROUP BY`、含 CTE），
与维度本身无关。所以同一个维度对 A 指标可用、对 B 指标被拒是正常的。

#### 全部拒绝规则，按**该找谁**分三类

**(a) 登记坏了 → 请数据负责人去修语义模型。** 不要重试，也不要绕路换指标 ——
换了只是把一个登记问题藏起来。

| rule | 含义 |
|---|---|
| `ambiguous_join` | 两实体间登记了**多条** JOIN 关系，无法确定用哪条（我们的 `sem_join` 无 `confidence` 字段，刻意不做静默选边） |
| `join_on_missing` | 该关系没填 `on_expr` —— 缺 ON 的 JOIN 就是笛卡尔积 |
| `join_on_invalid` | `on_expr` 不可信（恒真条件、引用了这两个实体之外的表）。恒真 ON 实测会让 `SUM` 从 524,147.88 虚增到 628,096.97 |
| `join_type_unsupported` | `join_type` 是 RIGHT/FULL/CROSS 或非字符串。RIGHT/FULL 会补出主实体侧不存在的行，CROSS 就是笛卡尔积，两者都改变聚合结果且不报错 |
| `join_plan_failed` | 快照的 `joins[]` 登记形态异常（读不出来），无法确认关联安全性 |
| `entity_physical_missing` | 维度所在实体未绑定物理表 |
| `join_alias_conflict` | 维度输出别名与指标 code 同名 —— 两列同名时分组标签会被指标值整列覆盖（实测 `SELECT 1 AS x, 2 AS x` → `{'x': 2}`），且不报错 |

**(b) 这个指标 / 这个口径形状就是拆不了 → 这是真答案。**
照 `allowed` 换用关联表那一侧的指标，或请数据负责人补一条能按该维度拆分的口径，
并如实告诉用户拆不了及原因。

| rule | 含义 |
|---|---|
| `fanout_refused` | 关系（定向到主实体侧之后）是一对多/多对多，而该指标是**会被行数放大**的聚合（`SUM`/`AVG`/`COUNT(*)`/`COUNT(col)`）。也含"聚合形态判不出来"的情形 —— 判不了按可能放大处理 |
| `not_decomposable` | 权威口径不是单层单表简单聚合：含 CTE / 子查询 / 多表 / 多聚合，或**口径自带 `GROUP BY`**，或解析不出唯一聚合，或 `measure_expression` 压根不是聚合 |
| `caliber_parse_failed` | 口径条件反解失败，或口径里**没有可重填的时间谓词**（那会编译出一条无时间过滤的关联查询：全历史汇总 + 全表扫描） |
| `multi_hop_unsupported` | 两实体间无直接关系（需多跳），或一次请求里的维度分属多个实体。只支持一跳，绝不生成 CROSS JOIN |

**(c) 请求里有不支持的东西 → 改请求后重试。**

| rule | 含义 |
|---|---|
| `join_filter_unsupported` | 跨实体下钻暂不支持附加 `filters` —— 过滤该落在主表还是关联表是业务判断，猜错不报错、只会让数字变了 |
| `join_multi_dimension_unsupported` | 同时点了跨实体维度与其他维度。一次只支持一个跨实体维度（同时编译会让后者连同它的分组静默消失） |
| `bind` | 分组列不是合法标识符，或口径里有非时间占位符填不上（如 `{month_start}`/`{region}`）。填参路对同一指标抛的是同一个错，join 路不会更宽松 |
| `join_compile_failed` | 编译期错误（如 `on_expr` 无法重写别名） |
| `no_join_needed` | 该维度其实就在主实体上 —— 这是分流判断有误，**不是登记坏了**，不必去找数据负责人（正常不会出现） |

#### ★ 拒绝就是答案，不要用相近指标凑数

`fanout_refused` / `not_decomposable` 说的是**「这个数按这个维度拆出来会是错的」**，
不是"再试一次"。改参数重问多少遍结果都一样。

**绝不要换一个名字相近的指标凑数** —— 那个数字看起来完全合理、用户无从察觉，
比如实说"拆不了"糟糕得多。这与 `did_you_mean` 那条同源：字面相近不等于口径相同。

#### 放行时也可能带风险提示

经**一对多**关系但指标是去重计数（`COUNT(DISTINCT …)`）时**放行**，
并在 `caliber.warnings` 里说明"经一对多关系取数、本指标为去重计数、结果不受行数
放大影响"。**有 warnings 必须一并转述给用户** —— 它是这次放行的唯一留痕。
反向登记的边被自动定向时也会留一条 warning。

### ⑤ execute_sql（2026-08-04）—— 探索旁路，**无口径背书**

执行**你自己写的**只读 SQL。开这条路的原因很具体：`query_indicator` 只覆盖**已登记
指标**，而数仓里的事实远多于登记过的指标。此前 agent 在三种情形下无路可走：

- 探索性核对（"这张表最近有数据吗""这个字段实际有哪些取值"）
- 权威口径覆盖不到的临时切片
- 上面那些 `fanout_refused` / `not_decomposable` 拒绝 —— 想看一眼底层数据都做不到

★ 无路可走的实际后果不是"agent 放弃"，而是**它去换一个名字相近的指标凑数**（上一节
警告的那件事）。给一条受**同样守卫**约束的裸 SQL 路，比让它绕路凑数安全。

#### 与 query_indicator 的分工（这条边界必须守住）

| | `query_indicator` | `execute_sql` |
|---|---|---|
| SQL 来源 | 已审核的口径 SQL，服务端填参 | **agent 自己写** |
| 口径可信度 | 权威（返回 `caliber` 声明） | **无背书**（返回里**没有** `caliber`） |
| 用途 | 报数字给用户 | 探索、核对、口径外切片 |

★★ **用户要业务数字（收入/用户数/订单量/转化率）时必须走 `query_indicator`。**
`execute_sql` 的 SQL 没人审核过，可能与公司口径不一致 —— 去重方式、时间归属、汇率、
是否排除测试账号与退款，全都是**口径决策**而非 SQL 技巧。拿它的数冒充权威数字是最糟
的失败方式：数字看起来完全合理、用户无从察觉，却会拿去做决策。确实只能用它时，
**必须如实说明「这是直接查底表得到的，未经口径审核」**。

#### 守卫与主链路完全同一套

白名单（只 SELECT/WITH）→ 写关键字全文扫描 → 多语句拒绝 → 自动补 LIMIT →
EXPLAIN 预检 → 只读账号。**一条都没有放宽**，判据直接复用同一个 `guard_readonly`
（有测试钉住它没有私抄一份）。唯一差异：守卫失败返回结构化错误而非抛异常 ——
裸 SQL 写错是意料之中，agent 需要读到原因去改。

#### 返回

与既有 `execute_sql` 同形：`data` / `columns` / `row_count` / `truncated` /
`duration_ms` / `executed_sql` / `dialect` / `mode`（恒为 `"sync"`）。

```json
{"mode": "sync",
 "columns": ["order_id", "final_revenue"],
 "data": [["100001879328163", "14.99"], ["100001884440283", "29.99"]],
 "row_count": 2, "truncated": false, "duration_ms": 4332}
```

- ★★ **`data` 的每一行是数组，不是字典** —— 值按 `columns` 逐位对齐，列名只在
  `columns` 里出现一次（与既有实现一致）。消费端按**下标**取值，给字典行不会报错，
  只会让表头正常而**每个单元格空白**。
- ★ **`columns` 在 0 行时也有值**。`columns` 非空而 `data` 为空 = 「该条件下确实没有
  数据」，这是**结论**，不要以为自己写错了列名去猜着重试。
- `duration_ms` 只计**查询本身**（不含守卫、EXPLAIN 预检与审计写入），
  所以它会略小于你观察到的整体响应时间。
- `truncated=true` 表示被 LIMIT 截断、结果不完整，须向用户说明。
- `dialect` 是当前配置的数仓方言（`starrocks` / `mysql` / `redshift`）。**别用别家语法**
  —— 不看方言就写，会被 EXPLAIN 拒了还不知道为什么，变成一轮轮无效重试。

失败时 `error_type` 四种：`permission_denied`（守卫拒绝，改成只读查询）/
`syntax_error`（EXPLAIN 没过，按库端报错改）/ `execution_failed`（真跑失败，缩小
时间窗或加过滤）/ `not_configured`（**服务端未配数仓连接**，改 SQL 重试无用，
如实告知用户是平台侧问题）。

#### 只做同步

不做 async/export（要一整套任务表 + 轮询端点 + S3）。故**不收 `mode` 参数** ——
留一个形同虚设的开关比没有更糟：agent 会传 `async` 然后等一个永不到来的结果。
需要大结果集时自己收窄条件或分批查。

#### ★★ 默认关闭，开启前请读这段

`SEM_RAW_SQL_ENABLE`，**默认 `False`**。未开启时返回
`error_type=not_configured`（归因到平台侧，agent 不会去改 SQL 重试）。

语义取数侧的方言与连接配置在后端 env 里单独设置：`SEM_QUERY_DIALECT` 支持
`starrocks` / `mysql` / `redshift`；Redshift 通常配 `SEM_QUERY_PORT=5439`。

默认关的理由是访问面：这条路能读**只读账号可见的任何表**，含「限制可见」模型背后的
物理表、以及根本没进任何语义模型的表。`SEM_QUERY_DB` 只是连接的默认库，写
`db.table` 全限定名照样跨库读。而模型级可见性作用在 `model_id` 上，裸 SQL 路
**没有 model_id**，那套隔离对它不适用（详见 §四·权限那节）。

所以：**真正的边界在 `SEM_QUERY_USER` 的数仓授权上，不在应用层。** 开启前请把该账号
的可见范围收窄到"你愿意让 agent 看到的表"。若你依赖模型级隔离做数据隔离，别开它。

#### 调用留痕（2026-08-05）

每次 `execute_sql` 调用都会写进 `sem_raw_sql_log`：调用方 / SQL 原文 / 实际执行的
SQL / **涉及的表** / 成败与 `error_type` / 行数 / 耗时。

- **被守卫拒绝的调用也记** —— `permission_denied` 反复出现说明有人在试探写操作，
  那才是审计里最该被看见的部分。
- **看审计需要独立权限** `module_semantic:rawsqllog:list`（不含在 `querylog:list`
  里）：审计含 SQL 原文，能看它等于能看到查询意图与表结构。
- 入口：同义词治理页的「裸 SQL 审计」Tab；另有
  `GET /semantic/rawsqllog/recent`。
- 保留 **90 天**（短于 query 日志的 180 —— 审计是事后追溯，且 SQL 原文不宜久留），
  随 `semantic_querylog_purge` 每周自动清理，也可手动
  `POST /semantic/rawsqllog/purge`。

★ 留痕是**旁路**：写日志失败不会让取数失败（有测试钉住）。所以审计**不是**访问控制
——它回答"读了什么"，不回答"能不能读"。后者仍然只由数仓账号授权决定。

## 三、为什么这套设计能压住幻觉

| 环节 | 谁在做 | 会幻觉吗 |
|---|---|---|
| 时间解析、检索、排序 | 规则 + 索引 | 否 |
| **NL→结构化意图** | **agent** | 可能，但被校验器夹住 |
| 取口径 | 查 Release 快照 | 否 |
| 校验（维度白名单/枚举/时间窗/draft/断层/粒度） | 规则引擎 | 否 |
| **SQL 生成** | **无 —— 取 `sql_text` 填占位符** | 否（根除） |
| 执行 | 只读 + LIMIT + EXPLAIN | 否 |
| **结果解读** | **agent（只述结果 + 强制带口径）** | 可能，只在措辞层 |

★ 上表说的是**主链路**（`query_indicator`）。`execute_sql` 那条旁路里 SQL 由 agent
自己写，所以"SQL 生成不会幻觉"这一条**在那条路上不成立** —— 它靠的是另一套约束：
执行守卫完全相同（拦不住"口径写错"，只拦得住"写坏数据/拖垮数仓"），
而口径可信度靠**工具说明明确声明结果无背书** + 要求 agent 如实转述。
这是一个有意的取舍：把"没有权威口径可用"这件事显式暴露出来，
好过让 agent 拿相近指标凑一个有背书外观的错数字。

`order_type = 'subscription'`、`renew_order_subtype = 'upgrade'` 这类口径条件写死在
权威 SQL 里，agent 碰不到，所以天然不会漏。

（原文举的 `is_paid_order = 1` 已随 2026-07-31 上游重构从物理表删除 —— 这恰好说明
另一件事：口径 SQL 会因上游改表而失效，故发布时有 `snapshot_audit` 守卫拦"引用了
模型未声明的列"。）

## 四、已知限制

- **长口语问法召回弱**：「有多少人付费」「order_class 是什么」这类含口语后缀的问法可能零命中。
  当前四级检索的前三路靠字面/分词匹配；向量路已实现但需配置 `AI_EMBEDDING_*` 端点才生效。
  规避：让用户说得更像指标名，或先用 `search_semantic` 试几个关键词。
- **数仓账号**：`SEM_QUERY_*` 必须是**只读账号**。代码层有 DDL/DML 守卫与自动 LIMIT，
  但只读性最终靠账号权限保证。
- **跨实体下钻的已知边界**：只走**一跳**、一次只**一个**跨实体维度、不带 `filters`、
  不做子查询预聚合改写（那要求系统自行决定按哪个键预聚合，属新的口径决策，必须人审）。
  另有一个已知怪癖：维度 code 去掉 `dim_` 前缀后若恰好是 SQL 关键字（如 `dim_set`
  → `set`），编译产物本身没问题，但会被执行前的只读守卫按关键字扫描拦下
  （`SqlGuardError … 'SET'`）。这是既有守卫的行为、**报错很响不静默**，
  规避办法是别用 SQL 关键字当维度 code 后缀。
- **检索副库**：`query_indicator` 与 `get_model_context` 只依赖 MySQL（读 Release 快照）；
  `search_semantic` 需要 PG 检索副库（`SEMANTIC_RETRIEVAL_ENABLE=true`）。
  副库不可用时检索返回 `unavailable`（**不是** `rejected` —— 那意味着"不在覆盖范围"），
  取数仍可用（前提是 agent 已知 code）。
  首次启用需跑 `python main.py init-retrieval --env=prod` 建表（运行时另有自愈兜底）。
- **MCP 鉴权**：非 dev 环境下未配置任何 token 会**拒绝挂载** `/mcp`（安全默认：
  无鉴权的 MCP 等于把数仓只读查询能力对外裸奔）。dev 环境保留免 token 的便利。

  三个来源，可并存（**库优先**）：
  - **`sem_mcp_token` 表**（主路径）：页面上签发/轮换/吊销，见「MCP 令牌」页
  - `SEM_MCP_TOKEN`：单 token（调用方记作 `default`），只读兜底
  - `SEM_MCP_TOKENS`：多 token，`"codata:tok1,ops:tok2"`（`name:token` 逗号分隔）。
    第三段可选：`name:token:username` —— 绑上用户名才有模型级用户隔离（见下）

  为什么保留 env 两项：现网正靠它们提供鉴权，升级瞬间让它们失效就是让所有接入方
  一起断 —— 那是安全倒退而非收紧。新增接入方请用页面签发。

  **一个接入方一个 token**：query 日志会记下调用方名（`caller` 字段），治理时才答得出
  "是谁在这么问" —— 自动巡检与真人提问的**采纳标准不同**（脚本里写死的措辞采纳成
  别名没有价值）；且单个 token 泄露可单独吊销，不必全量换、让所有接入方一起断。

  「同义词治理」页可**按调用方筛选**，候选与零召回都会显示贡献它的调用方
  （`callers`，复数 —— 同一说法可能被多个调用方问过）。

  请求头写 `Authorization: Bearer <token>`（大小写不敏感，裸 token 也接受）。
  token 比较用恒定时间函数，防时序侧信道。

- **MCP 不走 REST 的权限体系**：所有工具都没有 `AuthPermission`、且用
  `check_data_scope=False`（关闭数据权限过滤），也**无租户隔离**。风险边界分两种，
  取决于有没有开 `SEM_RAW_SQL_ENABLE`：

  **默认（`SEM_RAW_SQL_ENABLE=False`）**：agent **无法提交任意 SQL** —— 只能挑指标
  code + 时间窗 + 白名单内的维度，且执行侧有只读账号 + `guard_readonly` +
  自动 LIMIT + EXPLAIN 预检四层。

  **开启 `execute_sql` 后（2026-08-04 新增）**：★★ 上面那句**不再成立**。任何一个
  有效 token 都能执行自己写的只读 SQL，也就能读**只读账号可见的任何表** ——
  含「限制可见」模型背后的物理表、以及根本没进任何语义模型的表。
  `SEM_QUERY_DB` 只是连接的默认库，写 `db.table` 全限定名照样跨库读，**拦不住**。
  `SEM_QUERY_DIALECT` 决定当前方言（`starrocks` / `mysql` / `redshift`），Redshift 通常
  配 `SEM_QUERY_PORT=5439`。

  执行守卫仍然一条不少，但要看清它们防的是什么：只 SELECT/WITH、自动 LIMIT、
  EXPLAIN 预检防的是**写坏数据 / 拖垮数仓**，**不防"读到不该读的表"**。
  模型级可见性（下一条）作用在 `model_id` 上，而裸 SQL 路**没有 model_id**，
  所以那套隔离对它天然不适用。

  → 因此它**默认关闭**，且开启前应当把 `SEM_QUERY_USER` 的授权范围收窄到
  "你愿意让 agent 看到的表"。真正的边界在**数仓账号的授权**上，不在应用层。

- **模型级用户隔离**（可选，默认不启用）：默认一个 token 能看到平台上所有已发布的
  语义资产。要收窄就把模型设为**限制可见**：

  1. 语义模型页 →「编辑」→ 可见性改「限制可见」
  2. →「授权管理」把可见用户加进名单（权限点 `module_semantic:model:grant`，
     与 `model:update` **分开** —— 授权是改访问控制，不该由"能编辑模型的人"顺带获得）
  3. token 要在 `SEM_MCP_TOKENS` 里绑上用户名才有"人"的身份：
     `"codata:tok1:svc_codata,ops:tok2:zhangsan"`（`name:token:username`）

  判据：`public` 所有通过鉴权的调用方可见；`restricted` 仅 owner + 授权用户 + 超管。
  **未绑用户的 token（含旧的 `SEM_MCP_TOKEN`）只能看 public** —— 不当超级权限
  （那是漏洞），也不一概拒绝（那会让既有部署升级即断）。
  四个入口（检索 / hydrate / query_indicator / get_model_context）全部收口。
  ★ `execute_sql` **不在**这个收口范围内 —— 它没有 `model_id` 可判，
  见上一条。要靠模型级隔离时别开它。

## 五、就绪自检

`GET /semantic/retrieval/readiness`（权限 `module_semantic:retrieval:detail`）
回答"这套东西现在能用吗"。`ready: true` + `blockers: []` 即可用；
否则 `blockers` 逐条说明该做什么（未启用检索 / 未建表 / 尚未发布任何模型 /
索引已过期 / 数仓未配置或不通 / MCP 启用但未挂载）。

接入前先看它 —— 比让 agent 试一把再猜为什么失败快得多。
完整部署顺序见 `semantic-deploy-checklist.md`。
