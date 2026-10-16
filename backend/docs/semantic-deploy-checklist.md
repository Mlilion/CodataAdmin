# 语义平台部署清单

全新部署照此顺序走一遍，最后一步 `readiness` 全绿即上线完成。

**验收判据只有一条**：`GET /semantic/retrieval/readiness` 返回 `ready: true`、
`blockers: []`。前面每一步都是为了消掉一条 blocker——遇到问题先看 blockers 说什么，
它会直接告诉你该做哪一步。

---

## 0. 装依赖

```bash
pip install -r requirements.txt        # Docker 镜像用的就是这个
```

★ **不要**只装子集。`pgvector` / `jieba` / `fastmcp` 是 **import 期硬依赖**——
缺 `pgvector` 会让 `register_routers()` 直接 `ImportError`，**整个平台起不来**
（不只是检索降级），且 `SEMANTIC_RETRIEVAL_ENABLE=false` 挡不住（开关守连接，
守不住 import）。`tests/test_deploy_contract.py` 会在 CI 里守住这条。

Python 需 ≥ 3.12（`pyproject.toml: requires-python`；镜像用 `python:3.12-slim`）。

## 1. 起依赖容器

```bash
cd docker && docker compose -f docker-compose-example.yaml up -d mysql redis semantic-pg
```

`semantic-pg` 用 `pgvector/pgvector:pg17`（自带 vector 扩展，无需编译）。

## 2. 配 env

从 `env/.env.prod.example` 复制后**必须逐项确认**这几个：

| 配置 | 说明 |
|---|---|
| `SEM_RETRIEVAL_VECTOR_THRESHOLD` | 保持 `0.40`。**别放松**——0.65 会让无关问法召回付费率，gold set 实测拒答准确率 100%→0% |
| `SEM_MCP_TOKEN` | 可留空 —— **前提是**已在「MCP 令牌」页签发过令牌。库与 env 都没有任何令牌时，非 dev 环境会**拒绝挂载** `/mcp`（安全默认：无鉴权的 MCP 等于把数仓只读查询能力对外裸奔）。新增接入方建议走页面签发（可轮换/可吊销/看得到最后使用时间） |
| `SEM_QUERY_DIALECT` | 数仓方言：`starrocks` / `mysql` / `redshift`。Redshift 通常配 `SEM_QUERY_PORT=5439`；其余一般沿用 `9030` |
| `SEM_QUERY_*` | 数仓只读账号。**必须是只读账号**——代码守卫只是第二道闸。不配则 `query_indicator` 不可用 |
| `SEMANTIC_RETRIEVAL_ENABLE` | 要用检索（别名精确/全文/向量三路）就设 `true` |
| `SEM_PG_*` | 对应第 1 步的 `semantic-pg` |
| `SEM_RAW_SQL_ENABLE` | **默认 `False`，保持默认除非你读过下面这段。**开了它 agent 就能执行自己写的只读 SQL，访问面是「`SEM_QUERY_USER` 能看到的**任何**表」——含「限制可见」模型背后的物理表、以及根本没进任何语义模型的表。`SEM_QUERY_DB` 拦不住（那只是默认库，写 `db.table` 全限定名照样跨库读），模型级可见性也拦不住（它作用在 `model_id` 上，而裸 SQL 路没有 model_id）。**真正的边界在数仓账号的授权上，不在应用层** —— 开之前请把该账号的可见范围收窄到"你愿意让 agent 看到的表"。若你依赖模型级隔离做数据隔离，别开它 |

`SEM_QUERY_MAX_ROWS` / `SEM_QUERY_TIMEOUT` 现在**真的生效**（此前是死配置）。

## 3. 主库迁移与补丁

```bash
python main.py upgrade --env=prod                    # Alembic
# 再按时间顺序执行 sql/patches/*.sql（全部幂等，可重复跑）
```

## 4. 同步物理元数据（catalog）

前端 **语义中心 → 物理目录**，两条路二选一（看数据源从哪来）：

| 路径 | 接口 | 适用 |
|---|---|---|
| 「同步」 | `POST /semantic/catalog/sync` | 元数据由 DataSage 托管，填 DataSage 数据源 UUID |
| 「采集」 | `POST /semantic/catalog/collect` | 自己登记数据源直连数仓扫 `information_schema` |

自采集数据源支持 `starrocks` / `mysql` / `redshift`。Redshift 采集只扫 `information_schema`，默认数据库是 `dev`。

★ **这一步此前完全没写在清单里** —— 而它正是 `39cc17cc` 修的那个**绑定错乱 bug
一直没炸的原因（没人跑过同步）**：原实现每次同步都把列删光再插，自增主键被复用后
语义字段会静默绑到**另一张表的列**上（dev 实测 `pay_amount` 绑到了
`sys_user.username`）。现在同步是安全的（按 `(table_id, name)` upsert、
消失的列/表软删、软删行复活而不新插），且会产出**上游变更待办**。

返回里的 `changes_detected` 是本次检出的变更数（前端在「同步完成」的
toast 文案里带出来）。有变更时到 **语义中心 → 上游变更** 逐条处理：

- **绑定失效**（`bound`）—— 改语义字段的物理列绑定，或改实体绑的表
- **口径 SQL**（`sql_ref`）—— 改指标实现里的 SQL
- **仅表相关**（`table_only`）—— 通常只需确认

前两级还会进 `readiness.blockers`（第 12 步），所以**不处理完 `ready` 不会转 true**。

★ 忽略一条变更**必须写原因**：它是给下一个人看的。已关闭的待办不能重复关闭
（那会抹掉"谁在什么时候为什么关掉了它"）；上游变更再次被检出时会**自动回到待处理**
并在说明里追加一行重开记录。

建议加进每日定时（排在索引重建 04:10 之前，让当天索引基于新鲜元数据）。

## 5. 建检索副库表

```bash
python main.py init-retrieval --env=prod
```

幂等。未启用检索时它会打印提示并退出 0（那是正常路径，不是错误）。

副库表**不在** Alembic 迁移里——那是主库 MySQL 的迁移史，而副库是 PostgreSQL 的
派生副本（可随时 rebuild）。运行时另有自愈兜底，但部署时显式建一次能让"副库是否就绪"
在本清单里可验证，而不是等第一个用户来触发。

## 6. 起服务

```bash
python main.py run --env=prod
```

启动日志应看到：
- `✅ N 个系统周期任务已注册(...)` —— 含索引兜底重建、向量补齐、query 日志清理
- `✅ MCP 端点已挂载: /mcp`；若看到 `❌ 拒绝挂载 MCP` 就是第 2 步的 token 没配

## 7. 导入并**发布**语义模型

在语义中心建模型 → 补齐红灯项 → **点发布**。

★ 发布是硬前提：没有 Release 快照时 `hydrate` 报错、索引跳过、取数报"指标不存在"。
`readiness` 会明确报"尚未发布任何语义模型"。

发布时会自动跑口径 SQL 守卫（`snapshot_audit`）——若报"引用了模型未声明的列"，
说明上游改过表而口径 SQL 没同步，修完再发。
如果当前数仓方言是 Redshift，发布还会额外做兼容性审计；不兼容的函数会在发布前被拦住。

### 7b. ★ 必做：勾选跨实体维度进指标白名单，然后**重新发布**

**这一步是"要不要给用户跨实体下钻能力"的开关，不做则该能力等于没上线。**

前端 **语义中心 → 指标管理 → 编辑目标指标** →「可用维度」勾选那些
**不在该指标主实体上**的维度 → 保存 → 回 **语义模型**页**重新发布**（白名单进
Release 快照，不重发 agent 侧看不到）。

★ 这个路径核实过：「可用维度」控件只在**指标管理**页上
（`indicator/index.vue:119-120`）。语义模型页的指标节点是**只读检视面板**，
没有这个控件 —— 照"模型页里改指标"去点会找不到。

**跳过的后果**：受控两表 JOIN 编译代码全在，但"按用户城市看订阅收入"这类问法会在
校验阶段被 `dimension_allowed` 拒掉，压根走不到 JOIN 编译。
`indicator.available_dimension_ids` 是**纯人工维护**字段，代码里没有任何自动填充 ——
dev 实测**20/20 个指标的白名单里跨实体维度数 = 0**。

`readiness` **不会**因此变红（它检查的是"平台能不能用"，而不是"治理侧勾没勾维度"）——
所以这一步只能靠本清单和 `sql/patches/2026-08-04-semantic-cross-entity-dimension-note.sql`
里的说明记住。**「有能力但没人打开它」与「没有这个能力」对使用者是同一种体验。**

勾之前先确认这个（指标 × 维度）**组合**真的能用，否则勾了只是换一种拒绝理由：

| 要看的 | 不满足时的 rule |
|---|---|
| 两实体间有**恰好一条**已发布 JOIN 关系 | 多条 → `ambiguous_join`；没有 → `multi_hop_unsupported` |
| 关系定向到主实体侧后是 `many_to_one`/`one_to_one`；若是 `one_to_many`/`many_to_many` 则只有 `COUNT(DISTINCT …)` 类指标能用 | `fanout_refused`（实测经一对多做 `SUM`：524,147.88 → 628,096.97，虚增 19.8%，且 SQL 与 EXPLAIN 都不报错） |
| 指标口径是**单层单表简单聚合** | `not_decomposable`。含 CTE/子查询/多表/多聚合，或**口径自带 `GROUP BY`**（dev 的 `order_cnt` 就是 `SELECT order_type, COUNT(DISTINCT order_id) … GROUP BY order_type`），或是比值类指标 |

★★ **别按设计文档 §三 那句「17 个维度里 14 个完全可用」做预期** —— 那个数只数了
**维度**，没算指标侧的口径形态。dev 上绕过白名单穷举 20 指标 × 全部跨实体维度 =
**350 组**：215 组成功编译，**135 组被拒**（`not_decomposable` 85、`fanout_refused` 20、
`multi_hop_unsupported` 16、`bind` 14）。可用性是**组合**级别的，不是维度级别的。

建议先勾一个明确安全的组合（主实体 `many_to_one` 维度表，dev 的 `dim_user_*` /
`dim_date` 派生 / `dim_continent` 都是），用 `query_indicator` 实跑一次，
确认数字与 `caliber.warnings` 都合理，再批量铺开。

## 8. 建检索索引

前端 **系统设置 → AI 配置** →「重建索引」按钮（权限 `module_semantic:retrieval:rebuild`）。
索引过期时 blockers 清单旁边也会直接出现该按钮。

或走接口：

```bash
curl -X POST .../semantic/retrieval/rebuild -H "Authorization: Bearer <token>"
```

发布时会自动同步一次；本步是补救与首次全量。之后每日 04:10 有定时兜底。

★ 若返回 `skipped: true`，是 `SEMANTIC_RETRIEVAL_ENABLE=false`，什么都没做 —— 不是"重建完成"。

## 9. 配嵌入模型（可选，向量路）

前端 **系统设置 → AI 配置**：填 Base URL / API Key / 模型名 / 维度 → 测试连接 → 保存 → 补齐向量。

★ 维度填错是最常见的配置问题。页面会报 `vector_dim` 与 `dim_mismatch`；
向量路未生效时会直接给出**是哪个开关**的原因（env 熔断 / AI 配置未启用 / 端点未配全）。

## 10. 签发 MCP 令牌

前端 **语义中心 → MCP 令牌** →「签发令牌」（权限 `module_semantic:mcp:token`，
跑 `2026-08-02-semantic-mcp-token.sql` 后 SUPER_ADMIN/ADMIN 自动有）。

**明文只显示一次** —— 库里存不可逆哈希，关掉弹窗就取不回，丢了只能轮换重发。
一个接入方一个令牌（`caller` 进 query 日志，治理时才答得出"是谁在这么问"）。

★ 比改 env 好的地方：可单独轮换/吊销、看得到"最后何时被用过"（没有这个信息
没人敢删任何令牌，僵尸令牌只会越攒越多）。
★ env 的 `SEM_MCP_TOKEN` / `SEM_MCP_TOKENS` 仍然有效但只读，页面上会以
「配置文件(只读)」列出来 —— 免得以为它们已经不生效了。

**告诉接入方填哪个地址**：该页顶部直接给出这套部署的 MCP 地址（可复制），
签发弹窗里地址与令牌一起给全。不必让人去翻文档里的 `http://<host>:<port>/mcp` 占位符。

★ 地址默认**由请求推导**（你访问管理端用的 scheme + host 加 `/mcp`）。
**本平台在反向代理/网关后面时必须配 `SEM_MCP_PUBLIC_URL`** —— 否则页面上显示的
是内网地址或容器名，接入方抄走连不上。写站点根地址即可，`/mcp` 会自动补。
配了之后页面会标「由 SEM_MCP_PUBLIC_URL 指定」。

★ 页面还会显示该端点**此刻是否真在服务**。显示「未挂载」时那个地址会 404，
常见原因是 `SEM_MCP_ENABLE=false`，或非 dev 环境下一个令牌都没有（安全门禁）——
后者在本页签发令牌后**需要重启服务**才会挂上。

## 11. 收窄模型可见性（可选）

默认所有已发布模型对所有 MCP token 可见。要按人隔离：

1. 语义模型页 →「编辑」→ 可见性改**限制可见**
2. →「授权管理」加可见用户（权限 `module_semantic:model:grant`，
   跑 `2026-08-02-semantic-model-grant-permission.sql` 后 SUPER_ADMIN/ADMIN 自动有）
3. 令牌绑用户：「MCP 令牌」页签发时选「绑定用户」（或 env 写
   `SEM_MCP_TOKENS = "codata:tok1:svc_codata,ops:tok2:zhangsan"`）

★ 建议**先配授权名单再切限制可见** —— 反过来会出现"切换那一刻所有人都被挡在外面"。
★ 未绑用户名的 token 只能看 public 模型（既不当超级权限，也不让既有部署升级即断）。
模型负责人与超管无需出现在名单里。

## 12. 验收

```bash
curl .../semantic/retrieval/readiness -H "Authorization: Bearer <token>"
```

`ready: true` + `blockers: []` 即完成。它检查：

- 检索副库：启用 / 可达 / **已建表** / 向量列宽与维度一致
- **已发布模型数** > 0
- **索引新鲜度**：模型已发 v3 而索引仍 v2 会报出来（此前这种漂移完全不可见）
- **数仓连接**：真跑一次 `SELECT 1`（5 秒短超时）
- **MCP**：启用但因缺 token 未挂载会报出来
- **上游变更待办**：`bound` / `sql_ref` 级的 `pending` 条数（第 4 步）。
  `table_only` 不计入 —— 它通常只需确认，算进"不可用"会让 blockers 变成噪声。
  ★ 读取失败（如迁移没跑、`sem_upstream_change` 不存在）也**会**进 blockers，
  不会静默显示 `pending_high_impact: 0`

前端在 **系统设置 → AI 配置** 页顶部也会展示这份 blockers 清单。

### 12b. 裸 SQL 审计（仅在开了 `SEM_RAW_SQL_ENABLE` 时相关）

`readiness` **不检查**这一项 —— 它是可选能力，没开就不该报 blocker。开了的话验一次：

1. 经 MCP 打一次 `execute_sql`（比如 `SELECT 1`）；
2. 登录管理端 → 语义中心 →「同义词治理」→「**裸 SQL 审计**」Tab，应能看到刚那一条，
   且 `调用方` 列**不是空的**（空说明 MCP 令牌没绑上调用方名，或请求没带 Authorization）。

看不到这个 Tab 的两种原因，别混淆：
- **无权限**：`module_semantic:rawsqllog:list` 未授予 → Tab 整个不渲染（刻意不渲染空表格，
  空表格会被读成"没有数据"）。该权限点由
  `sql/patches/2026-08-05-semantic-raw-sql-log-permission.sql` 创建，**它依赖
  「同义词治理」菜单行已存在**（`route_name='SemanticQueryLog'`）—— 若那行不在，
  补丁会静默无事发生，表现为"补丁跑成功但端点 403"。
- **表没建**：`sql/patches/2026-08-05-semantic-raw-sql-log.sql` 没执行。

★ 保留期 90 天（短于 query 日志的 180 —— 审计含 SQL 原文，不宜久留），随
`semantic_querylog_purge` 每周自动清理；也可手动 `POST /semantic/rawsqllog/purge`。

---

## 常见问题

**检索返回"检索服务暂时不可用"** —— 服务端故障或未启用，不是"问的东西不在覆盖范围"。
查 `readiness` 的 `retrieval` 段。这两种情况在 agent 侧是分开的通道
（`unavailable` vs `rejected`），别混。

**agent 说"指标不存在"** —— 先确认模型**已发布**。未发布时会明确返回
`rule=model_not_released`；若确实是发布状态，才是指标 code 问题。

**取数报 `rule=not_configured`** —— `SEM_QUERY_*` 没配。这是服务端问题，
`retryable=false`，让 agent 重试无用。

**向量一直"待生成 N 条"** —— 看 AI 配置页的 `dim_mismatch` 与"向量未生效"原因。
补齐期只看 AI 配置的启用开关（不看查询期的 env 开关）——"先算好向量、再开查询开关"
是合法的上线顺序。

**定时任务没跑** —— 它们注册在 `SchedulerUtil.system_job_specs()`，不是任务管理页的
cronjob。**不要**在任务管理页把 `func` 填成函数的点分路径：那个字段存的是**代码块**，
由 `_task_wrapper` `exec()` 并要求定义 `handler()`，填路径会直接报错。
