# CodataAdmin Docker 部署说明

本文档说明 `docker/` 目录下的 Compose 部署方式。项目总览、本地开发和默认账号以 [根目录 README.md](../README.md) 为准。

## 目录结构

```txt
docker/
├── backend/
│   └── Dockerfile              # 后端镜像构建文件
├── mysql/
│   ├── init/
│   │   └── 01-bootstrap.sh     # MySQL 空数据目录首次启动时执行
│   └── data/                   # MySQL 数据目录
├── nginx/
│   ├── nginx.conf              # Nginx 配置
│   ├── ssl/                    # SSL 证书目录
│   └── web/                    # Web 构建产物挂载目录（dist 由 deploy.sh 生成）
├── postgres/
│   └── data/                   # 语义检索副库数据目录
├── redis/
│   └── data/                   # Redis 数据目录
├── .env.example                # Compose 环境变量模板
├── .env                        # Compose 环境变量文件，本机生成，不提交
├── docker-compose-example.yaml # 当前 Compose 示例
└── README.md
```

## 服务组成

| 服务 | 说明 |
|---|---|
| `mysql` | 主库，存放系统、平台和语义治理事实数据 |
| `redis` | 缓存、会话和限流等运行时数据 |
| `semantic-pg` | 可选语义检索副库，使用 PostgreSQL + pgvector |
| `backend-init` | 一次性后端初始化任务，创建表并导入基础数据 |
| `backend` | CodataAdmin API 和 MCP Server |
| `nginx` | Web 静态资源和反向代理入口 |
| `frontend-build` | profile-only 前端构建任务，显式运行后把 `frontend/web/dist` 同步到 `nginx/web/dist` |

## 环境变量

当前 Compose 使用本机的 `docker/.env` 作为环境变量文件。仓库只跟踪 `docker/.env.example`，首次部署先复制再编辑：

```bash
cp docker/.env.example docker/.env
cp backend/env/.env.prod.example backend/env/.env.prod
```

部署前请按目标环境修改密码、端口、站点域名和 `SECRET_KEY`。首次生产初始化还需要在 `backend/env/.env.prod` 设置至少 16 位的 `INITIAL_ADMIN_PASSWORD`；初始化完成后清除此密码。公网部署必须配置 HTTPS，并限制数据库、Redis 等基础服务端口的外部访问。

关键变量：

| 变量 | 说明 |
|---|---|
| `MYSQL_ROOT_PASSWORD` | MySQL root 密码 |
| `MYSQL_DATABASE` | 主库数据库名，建议按环境设置 |
| `MYSQL_USER` | 主库应用用户 |
| `MYSQL_PASSWORD` | 主库应用用户密码 |
| `MYSQL_PORT` | MySQL 暴露到宿主机的端口 |
| `REDIS_PASSWORD` | Redis 密码 |
| `REDIS_PORT` | Redis 暴露到宿主机的端口 |
| `SEM_PG_PASSWORD` | 语义检索副库密码；启用 `semantic-pg` 服务时必须设置 |
| `BACKEND_PORT` | 后端 HTTP 端口 |
| `HTTP_PORT` | Nginx HTTP 端口 |
| `DEPLOY_ENV` | 后端运行环境：`dev` / `prod` |

语义中心的只读数仓连接不在 `docker/.env` 里配，而是在 `backend/env/.env.*` 里配
`SEM_QUERY_DIALECT` / `SEM_QUERY_*`；其中 `SEM_QUERY_DIALECT` 支持 `starrocks` /
`mysql` / `redshift`，Redshift 通常用 `5439`。

Docker 内后端访问语义检索副库时使用容器服务名和容器端口：

```bash
SEM_PG_HOST = "semantic-pg"
SEM_PG_PORT = 5432
```

宿主机端口 `SEM_PG_PORT=5433` 只用于从宿主机直连调试 PostgreSQL。

## 首次初始化

Docker 首次部署采用两段初始化：

1. `mysql` 服务启动时挂载 `./mysql/init:/docker-entrypoint-initdb.d:ro`。当 `./mysql/data` 为空时，MySQL 官方 entrypoint 会执行 `01-bootstrap.sh`，只负责创建/校准数据库和字符集。
2. `backend-init` 等待 MySQL 健康后执行 `python main.py init-db --env=${DEPLOY_ENV:-prod}`，使用当前 ORM 模型创建表，并从 `backend/app/scripts/data/*.json` 导入基础数据。

普通重启不会重复执行 MySQL entrypoint 初始化脚本。`backend-init` 即使再次运行也会按表是否已有数据跳过，避免重复灌入基础数据。

历史 MySQL dump 仅可作为旧环境排查参考，不作为当前部署初始化来源；当前语义中心菜单和基础元数据以 JSON 种子为准。

## 一键部署

在项目根目录执行：

```bash
# 配置预检
./deploy.sh preflight

# 完整部署：使用当前本地代码，预检、构建 Web、构建后端镜像、启动并验证
./deploy.sh
```

完整部署不会执行 Git 拉取。请先由外部发布流程把代码更新到目标 commit，再运行脚本。脚本会自动运行 `frontend-build`，把 Vue 管理台产物写入 `docker/nginx/web/dist`，Nginx 随后从该目录服务 `/web`。

## 手动启动

```bash
./deploy.sh build-web
cd docker
docker compose --env-file .env -f docker-compose-example.yaml up -d
```

查看状态：

```bash
docker compose --env-file .env -f docker-compose-example.yaml ps
```

查看日志：

```bash
docker compose --env-file .env -f docker-compose-example.yaml logs -f backend-init backend
```

停止服务：

```bash
docker compose --env-file .env -f docker-compose-example.yaml down
```

## 常用维护

```bash
# 只重建 Web 管理台产物
../deploy.sh build-web

# 重新构建并启动后端
docker compose --env-file .env -f docker-compose-example.yaml up -d --no-deps --build backend

# 重新执行主库初始化任务
docker compose --env-file .env -f docker-compose-example.yaml run --rm backend-init

# 初始化语义检索副库
docker compose --env-file .env -f docker-compose-example.yaml exec backend \
  python main.py init-retrieval --env=${DEPLOY_ENV:-prod}
```

若需要完整重走首次初始化流程，必须先停止服务并清空 `docker/mysql/data`。这会删除现有主库数据，只应在全新部署或明确要重置环境时执行。

## 访问入口

| 服务 | 默认路径 |
|---|---|
| Web 管理台 | `/web` |
| Swagger | `/docs` |
| ReDoc | `/redoc` |
| API 健康检查 | `/common/health`、`/common/health/ready`、`/common/health/live` |
| MCP | `/mcp` |

## 安全建议

- 替换所有示例密码和密钥。
- 默认配置面向本地或受信任网络，包含 HTTP 和宿主机端口映射；公网或跨网段部署前必须配置 TLS、网关和端口访问控制。
- 持久化目录需要保证 Docker 进程可读写，常见权限问题可通过 `chown` 数据目录处理。
- 语义检索副库是派生索引，可按发布快照重建；主库仍是事实源。
