from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.concurrency import asynccontextmanager
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter, WebSocketRateLimiter

from app.core import cache_util

from .config.setting import settings
from .core.exceptions import handle_exception
from .core.http_limit import http_limit_callback, ws_limit_callback
from .core.logger import logger
from .scripts.initialize import InitializeData
from .utils.common_util import import_module, import_modules_async
from .utils.console import console_end, console_start


@asynccontextmanager
async def _mcp_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """嵌套 MCP 子应用的 lifespan（启动 Streamable HTTP session manager）。

    未启用或导入失败时直接放行 —— MCP 不该拖垮主应用启动。
    """
    if not settings.SEM_MCP_ENABLE:
        yield
        return
    try:
        from app.api.v1.module_semantic.mcp.server import mcp_http_app
    except Exception as e:  # noqa: BLE001
        logger.warning(f"⚠️ MCP lifespan 跳过(导入失败): {e}")
        yield
        return
    async with mcp_http_app.router.lifespan_context(app):
        logger.info("✅ MCP session manager 已启动")
        yield


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[Any, Any]:
    from app.api.v1.module_platform.tenant.service import TenantService
    from app.api.v1.module_system.dict.service import DictDataService
    from app.api.v1.module_system.params.service import ParamsService
    from app.core.ap_scheduler import SchedulerUtil

    try:
        await InitializeData().init_db()
        logger.info("✅ {}数据库初始化完成", settings.DATABASE_TYPE)
        await import_modules_async(modules=settings.EVENT_LIST, desc="全局事件", app=app, status=True)
        logger.info("✅ 全局事件模块加载完成")
        await ParamsService.init_cache(redis=app.state.redis)
        logger.info("✅ Redis系统参数初始化完成")
        await DictDataService.init_cache(redis=app.state.redis)
        logger.info("✅ Redis数据字典初始化完成")
        await TenantService.init_cache(redis=app.state.redis)
        logger.info("✅ Redis租户配置初始化完成")
        await SchedulerUtil.init_scheduler(redis=app.state.redis)
        logger.info("✅ 定时任务调度器初始化完成")
        await cache_util.init(redis=app.state.redis)
        logger.info("✅ fastapi-admin-cache 初始化完成")
        await FastAPILimiter.init(
            redis=app.state.redis,
            prefix=settings.REQUEST_LIMITER_REDIS_PREFIX,
            http_callback=http_limit_callback,
            ws_callback=ws_limit_callback,
        )
        logger.info("✅ 请求限流器初始化完成")

        console_start(
            host=settings.SERVER_HOST, port=settings.SERVER_PORT,
            reload=settings.ENVIRONMENT,
            database_ready=True, redis_ready=True,
            scheduler_ready=SchedulerUtil.is_running(), limiter_ready=True,
        )
    except Exception as e:
        logger.error("❌ 应用初始化失败: {}", e)
        raise SystemExit(1)

    # 预热库里的 MCP token 快照。
    # ★ 必须在这里而不是 register_mcp：那个函数是 sync 的（应用装配期），查不了库。
    # 鉴权本身跑在 sync 上下文（ASGI 中间件），只能读快照 —— 见 mcp/auth.py 模块注释。
    # 预热失败不影响启动：中间件每次请求都会调 ensure_tokens_fresh() 补上。
    try:
        from app.api.v1.module_semantic.mcp.auth import refresh_db_tokens

        count = await refresh_db_tokens()
        logger.info(f"✅ MCP token 快照已预热: {count} 个可用")
    except Exception as e:  # noqa: BLE001 - 预热失败不该拖垮启动
        logger.warning(f"⚠️ MCP token 快照预热失败(请求期会自动补): {e}")

    # MCP 的 Streamable HTTP session manager 由它自己的 lifespan 启动。
    # app.mount() 不会触发子应用的 lifespan —— 不嵌套进来的话，
    # 请求会得到 404（路由在但 session manager 没起）。这是实测踩到的坑。
    async with _mcp_lifespan(app):
        yield

    try:
        await SchedulerUtil.shutdown(wait=True)
        logger.info("✅ 定时任务调度器已关闭")
        await cache_util.clear()
        logger.info("✅ fastapi-admin-cache 已关闭")
        await FastAPILimiter.close()
        logger.info("✅ 请求限制器已关闭")
        await import_modules_async(modules=settings.EVENT_LIST, desc="全局事件", app=app, status=False)
        logger.info("✅ 全局事件模块卸载完成")
        from app.core.database import async_engine
        await async_engine.dispose()
        logger.info("✅ 数据库引擎连接池已释放")
        console_end()
    except Exception as e:
        logger.error("❌ 应用关闭过程中发生错误: {}", e)
        raise SystemExit(1)


def register_middlewares(app: FastAPI) -> None:
    for middleware in settings.MIDDLEWARE_LIST[::-1]:
        if not middleware:
            continue
        middleware = import_module(middleware, desc="中间件")
        app.add_middleware(middleware)


def register_exceptions(app: FastAPI) -> None:
    handle_exception(app)


def register_routers(app: FastAPI) -> None:
    from app.api.v1.module_common import common_router
    from app.api.v1.module_monitor import monitor_router
    from app.api.v1.module_platform import platform_router
    from app.api.v1.module_semantic import semantic_router
    from app.api.v1.module_system import system_router

    app.include_router(common_router, dependencies=[Depends(RateLimiter(times=200, seconds=10))])
    app.include_router(monitor_router, dependencies=[Depends(RateLimiter(times=200, seconds=10))])
    app.include_router(platform_router, dependencies=[Depends(RateLimiter(times=200, seconds=10))])
    app.include_router(system_router, dependencies=[Depends(RateLimiter(times=200, seconds=10))])
    app.include_router(semantic_router, dependencies=[Depends(RateLimiter(times=200, seconds=10))])

    from app.plugin.module_ai.chat.ws import WS_AI
    app.include_router(router=WS_AI, dependencies=[Depends(WebSocketRateLimiter(times=200, seconds=10))])
    
    from app.core.discover import get_dynamic_router, set_app_ref
    app.include_router(router=get_dynamic_router(), dependencies=[Depends(RateLimiter(times=200, seconds=10))])
    set_app_ref(app)

    register_mcp(app)


def register_mcp(app: FastAPI) -> None:
    """挂载 MCP 端点（外部 agent 如 Codata 经此调用检索/召回/取数）。

    独立挂在 /mcp（不走 ROOT_PATH 下的 REST 前缀）：MCP 是另一套协议，
    client 配置的是 http://host/mcp。鉴权走 Bearer token 中间件，不复用 REST 的 JWT。

    ★ 用 Starlette Route 直接注册而非 app.mount()：
    mount("/mcp", app) 会剥掉前缀让子应用收到空 path → 请求 /mcp/ 得 404；
    mount("/", app) 又会拦截所有 REST 请求。故取出内层 transport 注册两条路由
    （/mcp 与 /mcp/ 都收，client 写哪个都能连）。
    """
    if not settings.SEM_MCP_ENABLE:
        logger.info("ℹ️ MCP 端点未启用(SEM_MCP_ENABLE=false)")
        return
    # ★ 安全门禁：非 dev 环境 + 空 token = 拒绝挂载。
    # 默认姿态本来是危险的(SEM_MCP_ENABLE 默认 True 而 SEM_MCP_TOKEN 默认 ""，
    # prod.example 也发空 token)，而 mcp/auth.py 在空 token 时直接放行 ——
    # 等于把 query_indicator(任意参数化数仓只读查询)对外裸奔。
    # 用"拒绝挂载"而非"启动失败"：与本函数既有原则一致(MCP 失败不拖垮整个应用)，
    # 安全的失败方向是端点不存在。按 ENVIRONMENT 分级也与 scripts/seed_* 的姿态一致。
    env = getattr(settings.ENVIRONMENT, "value", settings.ENVIRONMENT)
    # 判据看**三个**来源: env 两项配置 + 库里发过的 token。
    # - 只看旧那项会让配了 SEM_MCP_TOKENS 的部署在 prod 反而挂不上端点
    # - 只看 env 会让"只在页面上发 token"的部署被判成无鉴权而拒绝挂载 ——
    #   于是新的 token 管理面在生产不可用, 反倒逼人回去改 env。
    #   门禁的本意是"挡无鉴权", 不是"挡库 token"。
    #
    # ★ 库那部分读的是**快照**(本函数是 sync 的, 查不了库)。快照由 lifespan 预热,
    #   而 lifespan 早于本函数执行完毕 —— 顺序见 app 装配流程。
    from app.api.v1.module_semantic.mcp.auth import has_any_configured_token

    if not has_any_configured_token() and env != "dev":
        logger.error(
            f"❌ 拒绝挂载 MCP: ENVIRONMENT={env} 且未配置任何 MCP token —— "
            f"无鉴权的 /mcp 会把语义口径与数仓只读查询能力对外裸奔。"
            f"请在「MCP 令牌」页签发一个 token，或配置 SEM_MCP_TOKEN / SEM_MCP_TOKENS，"
            f"或显式设 SEM_MCP_ENABLE=false 关闭该端点。"
        )
        return
    try:
        from starlette.routing import Route

        from app.api.v1.module_semantic.mcp.auth import MCPAuthMiddleware
        from app.api.v1.module_semantic.mcp.server import mcp_transport

        endpoint = MCPAuthMiddleware(mcp_transport)
        methods = ["GET", "POST", "DELETE"]
        for path in ("/mcp", "/mcp/"):
            app.router.routes.append(Route(path, endpoint=endpoint, methods=methods))
        logger.info("✅ MCP 端点已挂载: /mcp (Streamable HTTP)")
    except Exception as e:  # noqa: BLE001 - MCP 挂载失败不该让整个应用起不来
        logger.warning(f"⚠️ MCP 端点挂载失败，平台其余功能不受影响: {e}")


def app_has_mcp_route(app: FastAPI) -> bool:
    """`/mcp` 此刻到底在不在服务。

    ★ 读**真实路由表**而不是回放 `register_mcp` 的判据：那些判据有三条分支
    （开关、安全门禁、挂载异常），在别处重算一遍必然与实际漂移 ——
    而这个函数的用途正是"告诉接入方这个地址能不能连"，答错比不答更坏。
    """
    return any(getattr(r, "path", None) == "/mcp" for r in app.router.routes)

def register_files(app: FastAPI) -> None:
    if settings.STATIC_ENABLE:
        settings.STATIC_ROOT.mkdir(parents=True, exist_ok=True)
        app.mount(path=settings.STATIC_URL, app=StaticFiles(directory=settings.STATIC_ROOT), name=settings.STATIC_DIR)


def reset_api_docs(app: FastAPI) -> None:
    swagger_ui_redirect_url = str(app.swagger_ui_oauth2_redirect_url)
    root_openapi_url = str(app.root_path) + str(app.openapi_url)

    @app.get(swagger_ui_redirect_url, include_in_schema=False)
    async def swagger_ui_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    @app.get(settings.DOCS_URL, include_in_schema=False)
    async def custom_swagger_ui_html() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=root_openapi_url,
            title=app.title + " - Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_js_url=settings.SWAGGER_JS_URL,
            swagger_css_url=settings.SWAGGER_CSS_URL,
            swagger_favicon_url=settings.FAVICON_URL,
        )

    @app.get(settings.REDOC_URL, include_in_schema=False)
    async def custom_redoc_html():
        return get_redoc_html(
            openapi_url=root_openapi_url,
            title=app.title + " - ReDoc",
            redoc_js_url=settings.REDOC_JS_URL,
            redoc_favicon_url=settings.FAVICON_URL,
        )
