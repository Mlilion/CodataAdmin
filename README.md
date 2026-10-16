# CodataAdmin

> CodataAdmin 是面向企业数据分析场景的管理与语义治理平台，提供系统权限、租户运营、AI 配置、语义资产治理、模型发布、MCP 令牌、取数审计和检索副库管理能力。

简体中文 | [English](./README.en.md)

## 项目定位

CodataAdmin 不是单纯的后台管理模板，而是面向企业数据分析场景的管理与治理后台：

| 能力 | 说明 |
|---|---|
| 系统与权限 | 用户、角色、菜单、部门、岗位、参数、字典、公告、日志、文件、工单 |
| 平台运营 | 租户、套餐、插件、邮件、订单、发票、自助服务 |
| AI 与任务 | AI 配置、智能聊天、会话记忆、定时任务、工作流 |
| 语义治理 | 物理目录、业务域、业务实体、语义字段、语义模型、JOIN、指标、维度、知识库 |
| 可信取数 | 模型发布快照、语义检索、指标查询、只读 SQL 执行、MCP Server |
| 审计与运营 | 查询日志、裸 SQL 审计、上游变更待办、检索质量分析、服务健康检查 |

语义检索副库、嵌入模型、DataSage 同步和 MCP 接入都是可选能力；不开启时主平台仍可独立运行。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI、SQLAlchemy 2、Alembic、Pydantic v2、Typer、APScheduler、Redis、FastMCP |
| 前端 | Vue 3、Vite 7、TypeScript、Element Plus、Pinia、Tailwind CSS 4、ECharts、Axios |
| 主数据 | 默认 MySQL；后端配置层支持 PostgreSQL / SQLite |
| 语义检索 | 可选 PostgreSQL + pgvector，Python 侧 jieba 分词，OpenAI 兼容嵌入 |
| 集成 | DataSage 目录同步、第三方 OAuth、MCP 工具协议 |

## 快速启动

### 环境要求

- Python 3.12+
- uv
- Node.js 20.19+
- pnpm 9.x
- MySQL 8.0+ 和 Redis

### 本地开发

```bash
# 后端环境
cp backend/env/.env.dev.example backend/env/.env.dev
cd backend
uv sync
uv run main.py run --env=dev
```

```bash
# 前端环境
cp frontend/web/.env.example frontend/web/.env
cd frontend/web
corepack enable
pnpm install
pnpm dev
```

打开 `http://127.0.0.1:5180/web/#/login`。

开发环境初始化会创建演示账号（`super`、`admin`、`user`，默认密码 `123456`），仅供本地开发使用。生产环境不会使用这些演示账号：首次初始化时必须在 `backend/env/.env.prod` 设置至少 16 位的 `INITIAL_ADMIN_PASSWORD`，初始化程序会用它创建 `super` 管理员。请通过安全渠道提供该密码；初始化完成后从配置文件中移除，并在首次登录后更换。

## 数据初始化

本地后端首次启动会自动创建表并导入基础数据。也可以显式运行：

```bash
cd backend
uv run main.py init-db --env=dev
```

初始化数据来源是 `backend/app/scripts/data/*.json`，包含当前系统菜单、租户、套餐、用户、角色、字典、平台菜单以及语义中心相关菜单。历史 MySQL dump 不作为当前部署初始化来源。

语义检索副库是可选派生库，启用后可运行：

```bash
cd backend
uv run main.py init-retrieval --env=dev
```

Docker 首次部署由 `backend-init` 服务执行 `python main.py init-db --env=${DEPLOY_ENV:-prod}`。MySQL 的 `/docker-entrypoint-initdb.d` 脚本只在空数据目录首次初始化时执行，普通重启不会重复导入数据。

## Docker 部署

生产部署使用根目录的 `deploy.sh`，底层仍是 `docker/docker-compose-example.yaml`。代码版本由外部 Git/发布流程提前更新到生产机目标 checkout，`deploy.sh` 只使用当前本地代码，不会执行 `git pull`。首次部署先准备本机配置文件：

```bash
cp docker/.env.example docker/.env
cp backend/env/.env.prod.example backend/env/.env.prod
```

然后编辑 `docker/.env` 与 `backend/env/.env.prod`，替换数据库、Redis、语义检索副库密码以及 `SECRET_KEY`。`docker/.env` 不再提交到仓库，只保留在部署机器本地。

```bash
# 只做配置预检
./deploy.sh preflight

# 完整部署：使用当前本地代码，预检、构建 Web、构建镜像、启动服务
./deploy.sh

# 仅重新构建 Web 管理台产物
./deploy.sh build-web
```

Nginx 会服务 `/web` 并代理 `/api/v1`、`/static`、`/mcp`。默认配置面向本地或受信任网络；公网部署前请配置 HTTPS、限制数据库/Redis 等端口的外部访问，并审查防火墙与代理规则。如果语义检索副库启用，Docker 内后端应连接 `semantic-pg:5432`，不要使用宿主机映射端口 `5433`。

## 常用命令

```bash
# 后端
cd backend
uv run main.py run --env=dev
uv run main.py init-db --env=dev
uv run main.py revision --env=dev
uv run main.py upgrade --env=dev
uv run main.py init-retrieval --env=dev
uv run pytest
```

```bash
# 前端
cd frontend/web
pnpm dev
pnpm build
pnpm test
pnpm test:e2e
pnpm lint
```

```bash
# Docker
./deploy.sh preflight
./deploy.sh
# 仅更新前端产物：./deploy.sh build-web
cd docker
docker compose --env-file .env -f docker-compose-example.yaml logs -f backend-init backend
```

## 目录结构

```txt
.
├── backend/              # FastAPI 后端、语义中心、MCP Server、初始化数据
├── frontend/web/         # Vue 3 Web 管理台
├── frontend/app/         # 预留移动端目录
├── frontend/docs/        # 预留文档目录
├── docker/               # Docker Compose、MySQL/Redis/PostgreSQL 数据目录、Nginx 配置
├── docs/                 # 设计材料、方案文档、补充说明
└── deploy.sh             # 一键部署脚本
```

## 入口

| 服务 | 地址 |
|---|---|
| Web 管理台 | `http://127.0.0.1:5180/web/#/login` |
| 后端 API | `http://127.0.0.1:8001/api/v1` |
| Swagger | `http://127.0.0.1:8001/docs` |
| ReDoc | `http://127.0.0.1:8001/redoc` |
| MCP | `http://127.0.0.1:8001/mcp` |

## 文档入口

- [后端说明](backend/README.md)
- [前端 Web 说明](frontend/web/README.md)
- [Docker 部署说明](docker/README.md)
- [语义部署清单](backend/docs/semantic-deploy-checklist.md)
- [MCP 接入说明](backend/docs/semantic-mcp.md)
- [飞书 OAuth 说明](backend/docs/feishu-oauth.md)

## 许可证

本项目以 GNU GPL v3.0（GPL-3.0-only）授权，详见 [LICENSE](./LICENSE)。为保留上游源码中原有的版权和许可声明，旧版 MIT 许可文本也保存在 [LICENSES/MIT-legacy.txt](./LICENSES/MIT-legacy.txt)。

## 部署提醒

- 生产环境不要沿用初始化账号、默认密码或示例密钥。
- 主库基础数据以 ORM 和 JSON 种子为准；语义检索副库是可重建的派生索引。
- 语义模型需要发布后才会被 MCP 工具列出和消费。
