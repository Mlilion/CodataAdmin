# CodataAdmin Backend

基于 FastAPI 的后端服务，提供系统管理、平台运营、AI 配置、语义治理、MCP Server、任务调度和审计能力，为 CodataAdmin Web 管理台及外部 Agent 客户端提供 API。

> **与仓库根文档的关系**：项目总览、一键前后端启动、演示账号、Docker 部署、架构图与默认端口等请以 [根目录 README.md](../README.md) 为准；**本文档**侧重 `backend/` 目录结构、迁移命令与后端开发约定。

## 技术栈

| 技术 | 版本 | 说明 |
|------|------|------|
| FastAPI | 0.115.2 | Web 框架 |
| SQLAlchemy | 2.x | ORM 框架 |
| Alembic | 1.18.x | 数据库迁移工具 |
| Pydantic | 2.x | 数据验证与序列化 |
| APScheduler | 3.11.0 | 定时任务调度 |
| Redis | 7.x | 缓存与会话存储 |
| Uvicorn | 0.49.x | ASGI 服务器 |
| FastMCP | 2.x | MCP Server |
| SQLGlot / pgvector / jieba | - | 语义检索与 SQL 解析 |
| Python | 3.12+ | 运行环境 |

## 项目结构

```txt
backend/
├── app/                     # 项目核心代码
│   ├── alembic/             # 数据库迁移管理
│   ├── api/                 # API 接口模块
│   │   └── v1/              # API v1 版本
│   │       ├── module_system/   # 系统管理模块
│   │       ├── module_monitor/  # 系统监控模块
│   │       ├── module_ai/       # AI 功能模块
│   │       ├── module_semantic/ # 语义中心与 MCP
│   │       └── module_*/       # 其他业务模块
│   ├── common/              # 公共组件（常量、枚举、响应封装）
│   ├── config/              # 项目配置文件
│   ├── core/                # 核心模块（数据库、中间件、安全）
│   ├── module_task/         # 定时任务模块
│   ├── plugin/              # 插件模块（二开目录）
│   ├── scripts/             # 初始化脚本和数据
│   └── utils/               # 工具类（验证码、文件上传等）
├── env/                     # 环境配置文件
├── logs/                    # 日志输出目录
├── sql/                     # 历史 SQL 快照（当前初始化以 ORM + JSON 种子为准）
├── static/                  # 静态资源文件
├── main.py                  # 项目启动入口
├── alembic.ini              # Alembic 迁移配置
├── requirements.txt         # Python 依赖包
└── pyproject.toml           # 项目配置（uv / ruff）
```

### 模块分层

每个业务模块采用统一的分层结构：

```txt
module_*/
├── controller.py    # 控制器 - HTTP 请求处理
├── service.py       # 服务层 - 业务逻辑处理
├── crud.py          # 数据层 - 数据库操作
├── model.py         # ORM 模型 - 数据库表定义
├── schema.py        # Pydantic 模型 - 数据验证
└── param.py         # 参数模型 - 请求参数
```

代码按业务模块竖向拆分，HTTP、service、crud、model、schema、param 尽量在同一业务目录内演进。

## 快速开始

### 环境要求

- **Python**: 3.12+
- **数据库**: MySQL 8.0+ / PostgreSQL 13+ / SQLite 3.x（连接串在 `env/.env.dev`）
- **Redis**: 与 `.env.dev` 中配置一致

### 第一次在本机跑起来

1. 复制 `env/.env.dev.example` → `env/.env.dev`，填写数据库、Redis 等（先在 DB 中建好空库）。
2. 在 **`backend/` 目录下** 安装依赖：推荐 **`uv sync`**；或 `pip install -r requirements.txt`。
3. **启动**：`uv run main.py run --env=dev`（或 `python main.py run --env=dev`）。**首次启动会自动初始化数据库表与基础数据**，一般**无需**先执行 `upgrade`。接口文档示例：`http://127.0.0.1:8001/docs`（端口见 `.env.dev` 中 `SERVER_PORT`）。

基础数据也可手动初始化：

```bash
uv run main.py init-db --env=dev
```

该命令复用 `app/scripts/initialize.py`，从 `app/scripts/data/*.json` 加载当前基础数据；Docker 部署中的 `backend-init` 服务也调用它。

### 数据库迁移命令（模型变更时使用）

日常**首次启动不必手动执行**；当你**修改了 ORM 模型**并需用 Alembic 管理结构变更时再使用：

```bash
# 生成迁移文件（模型变更后）
python main.py revision --env=dev
# 应用迁移
python main.py upgrade --env=dev

# 使用 uv 时
uv run main.py revision --env=dev
uv run main.py upgrade --env=dev
```

### 语义检索副库

语义检索副库是可选 PostgreSQL + pgvector 派生库，不是主事实源。启用 `SEMANTIC_RETRIEVAL_ENABLE=true` 并配置 `SEM_PG_*` 后，可执行：

```bash
uv run main.py init-retrieval --env=dev
```

语义目录自采集支持 `starrocks` / `mysql` / `redshift`。连接参数在 `backend/env/.env.*` 里
配置 `SEM_QUERY_DIALECT`、`SEM_QUERY_HOST`、`SEM_QUERY_PORT`、`SEM_QUERY_USER` 和
`SEM_QUERY_PASSWORD`；Redshift 通常配 `5439`，默认库一般是 `dev`。

### 安装依赖与启动服务

```bash
# 推荐使用 uv（与 pyproject.toml 一致）
uv sync
uv run main.py run --env=dev

# 或使用传统 pip / venv
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py run --env=dev
```

### 代码格式化（ruff）

```bash
ruff check
ruff check --fix
ruff check --watch

# 使用 uv 时
uv run ruff check
uv run ruff check --fix
uv run ruff check --watch

# 生成开源授权函 JSON 文件
#uv run --with pip-licenses pip-licenses --format=json \
  > app/api/v1/module_platform/invoice/oss_licenses.json
```

## 后端约定（日期与序列化）

使用 **Pydantic v2** 与 **PostgreSQL（asyncpg）** 时：ORM 写入需要 Python 原生日期时间，JSON 输出需要可序列化字符串。

- 自定义 `DateStr` / `TimeStr` / `DateTimeStr`（`app/core/validator.py`）使用 **`PlainSerializer(..., when_used='json')`**
- `model_dump(mode='python')` 供 ORM 使用原生类型；JSON / Redis 使用 `model_dump(mode='json')`
- 统一 HTTP 响应见 `app/common/response.py` 中的 **`jsonable_encoder`**
- 写入 Redis 时请使用 **`model_dump(mode='json')`** 再序列化

## 相关链接

- **FastAPI 官方文档**: [https://fastapi.tiangolo.com/](https://fastapi.tiangolo.com/)
- **SQLAlchemy 文档**: [https://docs.sqlalchemy.org/](https://docs.sqlalchemy.org/)
- **Pydantic 文档**: [https://pydantic-docs.helpmanual.io/](https://pydantic-docs.helpmanual.io/)
