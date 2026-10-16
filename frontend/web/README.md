# CodataAdmin Web

CodataAdmin Web 是基于 Vue 3 + Vite + TypeScript + Element Plus 的管理台前端，覆盖系统管理、平台运营、AI、任务、语义中心、MCP 令牌和审计相关页面。

> **与仓库根文档的关系**：项目定位、演示账号、Docker 部署和整体启动方式请以 [根目录 README.md](../../README.md) 为准；本文档侧重 `frontend/web/` 的开发说明。

## 快速开始

### 环境要求

| 工具 | 版本 |
|---|---|
| Node.js | >= 20.19 |
| pnpm | >= 8.8，推荐 pnpm 9 |

未安装 pnpm 时可执行：

```bash
corepack enable
corepack prepare pnpm@9.15.3 --activate
```

### 安装并启动

```bash
cd frontend/web
cp .env.example .env
pnpm install
pnpm dev
```

默认开发端口由 `.env` 的 `VITE_PORT` 决定，当前示例为 `5180`。

## 与后端联调

1. 先启动 CodataAdmin 后端：`cd backend && uv run main.py run --env=dev`。
2. 前端开发服务器会通过 Vite proxy 将 `VITE_APP_BASE_API` 转发到 `VITE_API_BASE_URL`。
3. 默认后端地址为 `http://127.0.0.1:8001`，登录页为 `http://127.0.0.1:5180/web/#/login`。

若页面提示连接失败，先检查后端端口、`.env.development` 中的 `VITE_API_BASE_URL`，以及浏览器请求路径是否仍以 `/api/v1` 为前缀。

## 技术栈

| 类别 | 选型 |
|---|---|
| 框架 | Vue 3、Composition API、`<script setup>` |
| 构建 | Vite 7 |
| 语言 | TypeScript |
| UI | Element Plus、Iconify |
| 路由 | Vue Router Hash 模式，后端菜单驱动动态路由 |
| 状态 | Pinia、pinia-plugin-persistedstate |
| 样式 | Tailwind CSS 4、SCSS |
| 图表 | ECharts |
| HTTP | Axios |
| 国际化 | vue-i18n |

## 常用脚本

| 命令 | 说明 |
|---|---|
| `pnpm dev` | 本地开发 |
| `pnpm dev:force` | 强制预打包依赖后启动 |
| `pnpm build` | 类型检查并构建生产产物 |
| `pnpm preview` | 本地预览构建产物 |
| `pnpm type-check` | TypeScript 类型检查 |
| `pnpm test` | Vitest 单元测试 |
| `pnpm test:e2e` | Playwright E2E 测试 |
| `pnpm lint` | ESLint + Prettier + Stylelint |
| `pnpm clean:cache` | 清理 Vite 等缓存 |

## 目录结构

```txt
src/
├── api/              # 业务 API 封装
├── assets/           # 图片、字体、全局样式
├── components/       # 通用与业务组件
├── config/           # 应用配置
├── enums/            # 枚举
├── hooks/            # 组合式函数
├── layouts/          # 管理台布局
├── locales/          # i18n 资源
├── plugins/          # Vue 插件注册
├── router/           # 静态路由、动态路由、守卫
├── store/            # Pinia store
├── types/            # TypeScript 类型
├── utils/            # 工具函数
├── views/            # 页面模块
├── App.vue
└── main.ts
```

## 环境变量

只有 `VITE_` 前缀变量会注入前端代码：

| 变量 | 说明 |
|---|---|
| `VITE_PORT` | 开发服务器端口 |
| `VITE_BASE_URL` | 部署基础路径 |
| `VITE_APP_BASE_API` | API 路径前缀 |
| `VITE_API_URL` | 浏览器侧 API 根前缀 |
| `VITE_API_BASE_URL` | Vite proxy 目标后端地址 |
| `VITE_ACCESS_MODE` | 菜单与路由来源：`frontend` / `backend` / `mixed` |
| `VITE_APP_WS_ENDPOINT` | WebSocket 地址 |
| `VITE_APP_TITLE` | 页面标题 |

完整配置以 `.env`、`.env.development`、`.env.production` 和 `.env.example` 为准。修改 env 后需要重启 `pnpm dev`。

## 路由与菜单

| 文件 | 职责 |
|---|---|
| `src/router/staticRoutes.ts` | 静态路由与壳层菜单 |
| `src/router/dynamicRoutes.ts` | 动态路由定义 |
| `src/router/beforeEach.ts` | 登录态、权限和动态挂载 |
| `src/router/MenuProcessor.ts` | 后端菜单到前端路由记录的转换 |

新增业务页通常需要同步处理视图文件、后端菜单、权限标识和 i18n 文案。

## 构建与部署

- 生产产物输出到 `dist/`。
- 子路径部署时配置 `VITE_BASE_URL`，并让 Nginx 或网关转发 `/api`、WebSocket 和 MCP 相关请求到后端。
- 生产构建可能移除部分 `console`，以 `vite.config.ts` 中配置为准。
