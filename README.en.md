# CodataAdmin

> CodataAdmin is an administration and semantic governance platform for enterprise data analysis. It provides access control, tenant operations, AI settings, semantic assets, model releases, MCP tokens, query auditing, and retrieval infrastructure.

English | [简体中文](./README.md)

## Positioning

CodataAdmin is not just an admin template. It is the governance and control layer for enterprise data analysis workflows.

| Area | What it covers |
|---|---|
| System and access | Users, roles, menus, departments, positions, parameters, dictionaries, notices, logs, files, tickets |
| Platform operations | Tenants, packages, plugins, email, orders, invoices, self-service |
| AI and tasks | AI config, chat, memory, scheduled jobs, workflow |
| Semantic governance | Catalogs, domains, entities, semantic fields, semantic models, JOINs, indicators, dimensions, knowledge |
| Trusted querying | Model release snapshots, semantic retrieval, indicator queries, read-only SQL execution, MCP server |
| Audit and operations | Query logs, raw SQL audit, upstream change inbox, retrieval quality analysis, health checks |

Semantic retrieval, embeddings, DataSage sync, and MCP access are optional. The main platform can run without them.

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, Typer, APScheduler, Redis, FastMCP |
| Frontend | Vue 3, Vite 7, TypeScript, Element Plus, Pinia, Tailwind CSS 4, ECharts, Axios |
| Main data | MySQL by default; PostgreSQL / SQLite are supported by backend settings |
| Semantic retrieval | Optional PostgreSQL + pgvector, jieba tokenization in Python, OpenAI-compatible embeddings |
| Integrations | DataSage catalog sync, third-party OAuth, MCP tools |

## Quick Start

### Requirements

- Python 3.12+
- uv
- Node.js 20.19+
- pnpm 9.x
- MySQL 8.0+ and Redis

### Local Development

```bash
# Backend
cp backend/env/.env.dev.example backend/env/.env.dev
cd backend
uv sync
uv run main.py run --env=dev
```

```bash
# Frontend
cp frontend/web/.env.example frontend/web/.env
cd frontend/web
corepack enable
pnpm install
pnpm dev
```

Open `http://127.0.0.1:5180/web/#/login`.

Development initialization creates demo accounts (`super`, `admin`, and `user`, all with the default password `123456`) for local use only. Production initialization does not use these demo accounts. Set a one-time `INITIAL_ADMIN_PASSWORD` of at least 16 characters in `backend/env/.env.prod` before initializing a production database; this creates the `super` administrator. Provide the password through a secure channel, remove it from the config after initialization, and change it after the first login.

## Data Initialization

The backend creates tables and loads seed data on first local startup. You can also run it explicitly:

```bash
cd backend
uv run main.py init-db --env=dev
```

Seed data comes from `backend/app/scripts/data/*.json`, including current system menus, tenants, packages, users, roles, dictionaries, platform menus, and semantic center menus. Historical MySQL dumps are not used as the current deployment seed source.

Semantic retrieval is an optional derived PostgreSQL database. When enabled, initialize it with:

```bash
cd backend
uv run main.py init-retrieval --env=dev
```

For Docker deployment, the `backend-init` service runs `python main.py init-db --env=${DEPLOY_ENV:-prod}` before the long-running backend starts. MySQL entrypoint scripts under `/docker-entrypoint-initdb.d` only run when the MySQL data directory is empty.

## Common Commands

```bash
# Backend
cd backend
uv run main.py run --env=dev
uv run main.py init-db --env=dev
uv run main.py revision --env=dev
uv run main.py upgrade --env=dev
uv run main.py init-retrieval --env=dev
uv run pytest
```

```bash
# Frontend
cd frontend/web
pnpm dev
pnpm build
pnpm test
pnpm test:e2e
pnpm lint
```

```bash
# Docker
cd docker
docker compose --env-file .env -f docker-compose-example.yaml up -d
docker compose --env-file .env -f docker-compose-example.yaml logs -f backend-init backend
```

## Layout

```txt
.
├── backend/              # FastAPI backend, semantic center, MCP server, seed data
├── frontend/web/         # Vue 3 web console
├── frontend/app/         # Reserved mobile directory
├── frontend/docs/        # Reserved docs directory
├── docker/               # Docker Compose, data directories, Nginx config
├── docs/                 # Design materials and supporting docs
└── deploy.sh             # One-command deployment script
```

## Entry Points

| Service | URL |
|---|---|
| Web console | `http://127.0.0.1:5180/web/#/login` |
| Backend API | `http://127.0.0.1:8001/api/v1` |
| Swagger | `http://127.0.0.1:8001/docs` |
| ReDoc | `http://127.0.0.1:8001/redoc` |
| MCP | `http://127.0.0.1:8001/mcp` |

## Docs

- [Backend](backend/README.md)
- [Frontend Web](frontend/web/README.md)
- [Docker Deployment](docker/README.md)
- [Semantic Deployment Checklist](backend/docs/semantic-deploy-checklist.md)
- [MCP Integration](backend/docs/semantic-mcp.md)
- [Feishu OAuth](backend/docs/feishu-oauth.md)

## License

This project is licensed under GNU GPL v3.0 (GPL-3.0-only). See [LICENSE](./LICENSE). The prior MIT notice is retained in [LICENSES/MIT-legacy.txt](./LICENSES/MIT-legacy.txt) for attribution to upstream portions.

## Deployment Notes

- Do not reuse initial accounts, default passwords, sample secrets, or local port exposure rules in production.
- Main database seed data is driven by ORM models and JSON files; semantic retrieval is a rebuildable derived index.
- Semantic models must be released before MCP tools can list or consume them.
