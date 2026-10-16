import os
from typing import Annotated

import typer
import uvicorn
from alembic.config import Config
from fastapi import FastAPI

from alembic import command
from app.common.enums import EnvironmentEnum
from app.utils.banner import worship

fastapiadmin_cli = typer.Typer()
alembic_cfg = Config("alembic.ini")


def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例并完成日志、中间件、路由与静态资源注册。

    返回:
    - FastAPI: 已配置生命周期的应用对象。
    """
    from app.config.setting import settings
    from app.init_app import lifespan, register_exceptions, register_files, register_middlewares, register_routers, reset_api_docs
    # 创建FastAPI应用
    app = FastAPI(**settings.FASTAPI_CONFIG, lifespan=lifespan)
    # 注册异常处理器
    register_exceptions(app)
    # 注册中间件
    register_middlewares(app)
    # 注册路由
    register_routers(app)
    # 注册静态文件
    register_files(app)
    # 重设API文档
    reset_api_docs(app)
    return app


# typer.Option是非必填；typer.Argument是必填
@fastapiadmin_cli.command(
    name="run",
    help="启动 CodataAdmin 服务, 运行 uv run main.py run --env=dev 不加参数默认 dev 环境",
)
def run(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
) -> None:
    """
    按指定环境加载配置并启动 Uvicorn（开发环境开启 reload）。

    参数:
    - env (EnvironmentEnum): 运行环境，对应 `--env`。

    返回:
    - None
    """

    # 设置环境变量（必须在 import settings 之前，确保加载正确环境）
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import settings
    from app.core.logger import logger

    typer.secho(
        message="CodataAdmin 服务启动",
        fg=typer.colors.GREEN,
    )
    logger.info(worship(env.value))

    # 启动uvicorn服务
    uvicorn.run(
        app="main:create_app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=env.value == EnvironmentEnum.DEV.value,
        factory=True,
        log_config=None,
    )


@fastapiadmin_cli.command(
    name="revision",
    help="生成新的 Alembic 迁移脚本, 运行 python main.py revision --env=dev",
)
def revision(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
) -> None:
    """
    使用 Alembic 自动生成迁移脚本（autogenerate）。

    参数:
    - env (EnvironmentEnum): 运行环境，用于加载对应数据库模型元数据。

    返回:
    - None
    """
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import get_settings

    get_settings.cache_clear()
    command.revision(alembic_cfg, autogenerate=True, message="迁移脚本")
    typer.echo("迁移脚本已生成")


@fastapiadmin_cli.command(
    name="upgrade",
    help="应用最新的 Alembic 迁移, 运行 python main.py upgrade --env=dev",
)
def upgrade(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
) -> None:
    """
    将数据库升级到 Alembic 最新版本（head）。

    参数:
    - env (EnvironmentEnum): 运行环境。

    返回:
    - None
    """
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import get_settings

    get_settings.cache_clear()
    command.upgrade(alembic_cfg, "head")
    typer.echo("所有迁移已应用。")


@fastapiadmin_cli.command(
    name="init-db",
    help="初始化主库表结构与基础数据, 运行 python main.py init-db --env=prod",
)
def init_db(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
) -> None:
    """
    初始化主库表结构和基础种子数据。

    Docker 首次部署的 backend-init 服务调用本命令。它复用应用启动时的
    InitializeData 流程，避免恢复过期 SQL dump 导致表结构与菜单权限漂移。

    参数:
    - env (EnvironmentEnum): 运行环境。

    返回:
    - None
    """
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import get_settings

    get_settings.cache_clear()

    import asyncio

    from app.config.setting import settings
    from app.core.base_model import MappedBase
    from app.core.logger import logger
    from app.utils.import_util import ImportUtil

    async def _run() -> None:
        # 独立 init-db 命令不会经过 create_app/register_routers，需主动导入所有模型，
        # 否则 create_all 只能看到 InitializeData 模块直接 import 的基础表。
        found_models = ImportUtil.find_models(MappedBase)
        logger.info(f"主库初始化已加载 ORM 模型: {len(found_models)} 个")

        from app.scripts.initialize import InitializeData

        await InitializeData().init_db()

        from app.core.database import async_engine, engine

        await async_engine.dispose()
        engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as e:  # noqa: BLE001 - CLI 要给部署者可行动的错误
        typer.echo(
            f"✗ 主库初始化失败: {e}\n"
            f"  请确认 MySQL 可达且 DATABASE_* 配置正确 "
            f"(host={settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME})",
            err=True,
        )
        raise typer.Exit(code=1) from e

    typer.echo(
        f"✅ 主库表结构与基础数据已初始化 "
        f"({settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME})"
    )


@fastapiadmin_cli.command(
    name="init-retrieval",
    help="初始化语义检索副库(PG: vector 扩展 + 检索表 + 向量索引, 幂等), "
         "运行 python main.py init-retrieval --env=dev",
)
def init_retrieval(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
) -> None:
    """建立语义检索副库的表结构（部署清单里发布模型之前的一步）。

    为什么需要显式命令：副库表既不在 Alembic 迁移里（那是主库 MySQL 的迁移史），
    也不在 `lifespan()` 里（可选功能失败不得让整个平台起不来）。运行时虽有自愈兜底，
    但部署时显式建一次能让"副库是否就绪"在上线清单里可验证，而不是等第一个用户来触发。

    幂等：CREATE EXTENSION / create_all / CREATE INDEX 全部 IF NOT EXISTS。

    参数:
    - env (EnvironmentEnum): 运行环境。

    返回:
    - None
    """
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import get_settings

    get_settings.cache_clear()
    from app.config.setting import settings

    if not settings.SEMANTIC_RETRIEVAL_ENABLE:
        # 不是错误：未启用该功能时跳过是正常部署路径，故退出码 0
        typer.echo(
            "⏭  SEMANTIC_RETRIEVAL_ENABLE=false，跳过副库初始化。\n"
            "   如需启用检索(别名精确/全文/向量三路)，请配置 SEMANTIC_RETRIEVAL_ENABLE=true "
            "与 SEM_PG_* 连接项后重跑本命令。"
        )
        return

    import asyncio

    from app.api.v1.module_semantic.retrieval.database import (
        dispose_retrieval_db,
        init_retrieval_db,
    )

    async def _run() -> None:
        try:
            await init_retrieval_db()
        finally:
            await dispose_retrieval_db()

    try:
        asyncio.run(_run())
    except Exception as e:  # noqa: BLE001 - CLI 要给出可行动的错误而非栈
        typer.echo(
            f"✗ 副库初始化失败: {e}\n"
            f"  请确认 PG 可达且 SEM_PG_* 配置正确 "
            f"(host={settings.SEM_PG_HOST}:{settings.SEM_PG_PORT}/{settings.SEM_PG_DB})",
            err=True,
        )
        raise typer.Exit(code=1) from e

    typer.echo(
        f"✅ 语义检索副库已就绪 "
        f"({settings.SEM_PG_HOST}:{settings.SEM_PG_PORT}/{settings.SEM_PG_DB})\n"
        f"   下一步：发布语义模型 → POST /semantic/retrieval/rebuild 建索引"
    )


if __name__ == "__main__":
    fastapiadmin_cli()
