"""部署契约：requirements.txt 与 env example 不得与真实依赖/默认值漂移。

## 为什么需要这一层

现有全部测试都跑在**装齐 dev 依赖的 venv** 里，因此它们**结构上不可能发现**
"requirements.txt 漏了一个包" —— 771 个测试全绿的同时，`pip install -r requirements.txt`
的全新部署可以是完全起不来的。

真实踩到（2026-08-01）：`pyproject.toml` 声明了 pgvector / jieba / fastmcp，
`requirements.txt` 三个都没有，而官方 `docker/backend/Dockerfile:14,17` 装的正是
requirements.txt。后果不是"检索功能降级"而是**整个应用起不来**：

    register_routers()                    app/init_app.py:119
      → module_semantic/__init__.py       from .retrieval import RetrievalRouter
        → retrieval/controller.py         from .database import retrieval_status
          → retrieval/database.py         from .model import RetrievalMappedBase
            → retrieval/model.py:13       from pgvector.sqlalchemy import Vector  ← ImportError

这条链路上没有任何 try/except，且 `register_routers` 在 `create_app()` 里同步调用。
`SEMANTIC_RETRIEVAL_ENABLE=false` 也挡不住 —— 开关守的是连接，守不住 import。

## 这些测试的价值在未来

修那一次只要改两行；本文件真正防的是**下次再加依赖/改默认值时忘了同步**。
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent


def _canon(name: str) -> str:
    """包名归一化：PEP 503（大小写、下划线/点/连字符等价）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pkg_name(spec: str) -> str:
    """从依赖声明里剥出包名：`sqlglot[rs]==27.8.0` → `sqlglot`。"""
    return _canon(re.split(r"[\[<>=!~;\s]", spec.strip(), maxsplit=1)[0])


def _pyproject_deps() -> set[str]:
    data = tomllib.loads((_BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return {_pkg_name(d) for d in data["project"]["dependencies"] if d.strip()}


def _pyproject_specs() -> dict[str, str]:
    data = tomllib.loads((_BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        _pkg_name(spec): spec
        for spec in data["project"]["dependencies"]
        if spec.strip()
    }


def _requirements_deps() -> set[str]:
    out: set[str] = set()
    for line in (_BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.add(_pkg_name(line))
    return out


def _requirements_specs() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (_BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines():
        spec = line.split("#", 1)[0].strip()
        if spec and not spec.startswith("-"):
            out[_pkg_name(spec)] = spec
    return out


def _docker_compose() -> dict:
    return yaml.safe_load((_ROOT / "docker" / "docker-compose-example.yaml").read_text(encoding="utf-8"))


def _shell_function_body(script_text: str, function_name: str) -> str:
    match = re.search(
        rf"^{re.escape(function_name)}\(\) \{{(?P<body>.*?)^}}",
        script_text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"deploy.sh 缺少 {function_name}() 函数"
    return match["body"]


# ---------------------------------------------------------------- 依赖契约


def test_requirements_covers_pyproject_dependencies():
    """★★ requirements.txt 必须覆盖 pyproject 的每个生产依赖。

    requirements.txt 的头一行自称"与 pyproject.toml [project] dependencies 一致" ——
    这条测试让那句话变成可执行的约束，而不是一句会过期的注释。
    """
    missing = sorted(_pyproject_deps() - _requirements_deps())

    assert not missing, (
        f"requirements.txt 缺少 pyproject 声明的生产依赖: {missing}\n"
        f"★ Dockerfile 装的是 requirements.txt(docker/backend/Dockerfile:17)，"
        f"缺包会让容器部署直接起不来 —— 现有测试跑在装齐依赖的 venv 里，发现不了。"
    )


@pytest.mark.parametrize("pkg", ["pgvector", "jieba", "fastmcp", "redshift_connector"])
def test_semantic_hard_dependencies_are_pinned(pkg):
    """★ 语义平台的硬依赖（import 期就需要）单独钉住。

    它们容易被当成"可选增强"而漏掉：pgvector 是 ORM 列类型(retrieval/model.py:13)、
    jieba 在分词器模块级、fastmcp 在 MCP server 模块级；Redshift 支持的 connector
    同样随语义模块能力发布进入生产镜像。任一缺失都不是运行期降级。
    """
    assert _canon(pkg) in _requirements_deps(), (
        f"{pkg} 不在 requirements.txt 中 —— 它是 import 期硬依赖，缺了应用起不来"
    )


def test_redshift_connector_version_is_exactly_pinned():
    """Redshift driver 必须在两份生产依赖清单里精确锁版。"""
    assert _pyproject_specs().get(_canon("redshift_connector")) == "redshift_connector==2.1.16"
    assert _requirements_specs().get(_canon("redshift_connector")) == "redshift_connector==2.1.16"


def test_semantic_module_imports_standalone():
    """★★ 语义模块能独立 import（冒烟：等价于"路由能注册"）。

    这是对上面依赖契约的行为验证：契约测试查清单，这条真的跑一遍 import。
    """
    import importlib

    mod = importlib.import_module("app.api.v1.module_semantic")

    assert hasattr(mod, "semantic_router"), "语义路由未导出"


def test_init_db_command_exists():
    """Docker 首次初始化 job 依赖显式 init-db 命令。"""
    from typer.testing import CliRunner

    from main import fastapiadmin_cli

    result = CliRunner().invoke(fastapiadmin_cli, ["init-db", "--help"])

    assert result.exit_code == 0
    assert "--env" in result.stdout


def test_docker_compose_runs_backend_initializer_before_backend():
    """Docker 部署必须先跑后端初始化 job，再启动常驻 backend 服务。"""
    services = _docker_compose()["services"]

    assert "backend-init" in services, "缺少 backend-init，一键部署无法显式初始化基础数据"

    init_service = services["backend-init"]
    assert init_service["restart"] == "no"
    assert init_service["command"] == ["python", "main.py", "init-db", "--env", "${DEPLOY_ENV:-prod}"]
    assert init_service["depends_on"]["mysql"]["condition"] == "service_healthy"
    assert init_service["environment"]["DATABASE_HOST"] == "mysql"
    assert init_service["volumes"] == ["../backend:/home"]

    backend_depends = services["backend"]["depends_on"]
    assert backend_depends["backend-init"]["condition"] == "service_completed_successfully"


def test_semantic_pg_fixture_default_matches_compose_host_port():
    """语义检索测试默认端口必须和 Docker Compose 的默认映射一致。"""
    fixture_text = (
        _BACKEND / "tests" / "module_semantic" / "conftest.py"
    ).read_text(encoding="utf-8")
    fixture_match = re.search(
        r'TEST_SEM_PG_PORT",\s*"(?P<port>\d+)"', fixture_text
    )
    assert fixture_match, "无法从语义检索 fixture 读取 TEST_SEM_PG_PORT 默认值"

    compose_port = _docker_compose()["services"]["semantic-pg"]["ports"][0]
    compose_match = re.search(r"\$\{SEM_PG_PORT:-(?P<port>\d+)\}:5432", compose_port)
    assert compose_match, "无法从 Compose 读取 semantic-pg 默认宿主机端口"

    assert fixture_match["port"] == compose_match["port"]


def test_deploy_script_targets_current_repository_and_compose_file():
    """部署脚本使用当前仓库和正确的 Compose 文件。"""
    deploy_text = (_ROOT / "deploy.sh").read_text(encoding="utf-8")

    assert 'PROJECT_NAME="CodataAdmin"' in deploy_text
    assert "docker-compose-example.yaml" in deploy_text
    assert "gitee.com/fastapiadmin" not in deploy_text
    assert "docker compose --env-file" in deploy_text


def test_deploy_script_uses_existing_local_checkout():
    """正式部署不能在部署过程中重新拉取或初始化代码。"""
    deploy_text = (_ROOT / "deploy.sh").read_text(encoding="utf-8")
    full_deploy_body = _shell_function_body(deploy_text, "full_deploy")
    deps_body = _shell_function_body(deploy_text, "check_deps")

    assert "pull_code()" not in deploy_text
    assert "git pull" not in deploy_text
    assert "git fetch" not in deploy_text
    assert "git init" not in deploy_text
    assert "GIT_REPO" not in deploy_text
    assert "log_local_revision()" in deploy_text
    assert "log_local_revision" in full_deploy_body
    assert 'for cmd in docker;' in deps_body
    assert "git" not in deps_body


def test_deploy_script_builds_frontend_before_service_restart():
    """完整部署必须先构建 Web 管理台产物，再重启服务。"""
    deploy_text = (_ROOT / "deploy.sh").read_text(encoding="utf-8")
    full_deploy_body = _shell_function_body(deploy_text, "full_deploy")

    assert "build_frontend()" in deploy_text
    assert "docker/nginx/web/dist/index.html" in deploy_text
    assert "compose run --rm frontend-build" in deploy_text
    assert "build_frontend" in full_deploy_body
    assert "build_image" in full_deploy_body
    assert "stop_service" in full_deploy_body
    assert full_deploy_body.index("build_frontend") < full_deploy_body.index("stop_service")
    assert full_deploy_body.index("build_image") < full_deploy_body.index("stop_service")


def test_deploy_script_validates_prod_runtime_env():
    """生产部署必须在启动前检查后端 env 与明显的示例密钥。"""
    deploy_text = (_ROOT / "deploy.sh").read_text(encoding="utf-8")
    full_deploy_body = _shell_function_body(deploy_text, "full_deploy")

    assert "check_prod_env_file()" in deploy_text
    assert 'BACKEND_ENV_FILE="${BACKEND_ENV_DIR}/.env.${DEPLOY_ENV:-prod}"' in deploy_text
    assert "SECRET_KEY" in deploy_text
    assert "CHANGE_ME" in deploy_text
    assert "your_" in deploy_text
    assert "fastapiadmin123" in deploy_text
    assert "check_docker_env_placeholders()" in deploy_text
    assert "preflight()" in deploy_text
    assert "check_prod_env_file" in full_deploy_body
    assert "check_docker_env_placeholders" in full_deploy_body


def test_docker_env_is_local_only_with_tracked_example():
    """docker/.env 必须只留在部署机器本地，仓库只跟踪示例文件。"""
    ignore_text = (_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "docker/.env" in ignore_text
    assert (_ROOT / "docker" / ".env.example").exists()

    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "docker/.env"],
        cwd=_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0, "docker/.env 仍被 Git 跟踪，生产密钥会被提交"


def test_docker_compose_has_profile_only_frontend_build_service():
    """Compose 应提供显式前端构建 profile，普通 up 不自动跑。"""
    services = _docker_compose()["services"]
    build_service = services["frontend-build"]

    assert build_service["profiles"] == ["build"]
    assert build_service["image"].startswith("node:20")
    assert build_service["working_dir"] == "/app"

    command = build_service["command"]
    assert "corepack enable" in command
    assert "pnpm install --frozen-lockfile" in command
    assert "pnpm build" in command
    assert "cp -a dist /web/dist" in command

    volumes = build_service["volumes"]
    assert "../frontend/web:/app" in volumes
    assert "./nginx/web:/web" in volumes
    assert "frontend_node_modules:/app/node_modules" in volumes


def test_deploy_script_prepares_all_bind_mount_runtime_dirs():
    """部署前要创建所有本地 bind mount 目录，包含语义 PG 与 Web 产物目录。"""
    deploy_text = (_ROOT / "deploy.sh").read_text(encoding="utf-8")

    assert '"${DOCKER_DIR}/mysql/data"' in deploy_text
    assert '"${DOCKER_DIR}/redis/data"' in deploy_text
    assert '"${DOCKER_DIR}/postgres/data"' in deploy_text
    assert '"${DOCKER_DIR}/nginx/web"' in deploy_text


def test_prod_env_example_uses_compose_container_addresses():
    """Docker 生产 env 示例应使用容器内服务名和端口，而不是宿主机端口。"""
    text = (_BACKEND / "env" / ".env.prod.example").read_text(encoding="utf-8")

    assert 'SECRET_KEY = "CHANGE_ME_generate_a_long_random_secret"' in text
    assert 'INITIAL_ADMIN_PASSWORD = ""' in text
    assert "DEMO_ENABLE = False" in text
    assert 'DATABASE_HOST = "mysql"' in text
    assert 'DATABASE_USER = "codataadmin"' in text
    assert 'DATABASE_NAME = "codataadmin"' in text
    assert 'REDIS_HOST = "redis"' in text
    assert 'SEM_PG_HOST = "semantic-pg"' in text
    assert "SEM_PG_PORT = 5432" in text
    assert 'SEM_PG_HOST = "localhost"' not in text


def test_bill_importer_does_not_depend_on_developer_absolute_path():
    """账单语义导入器必须使用仓库相对默认路径或环境变量覆盖。"""
    helper_text = (
        _BACKEND / "scripts" / "_bill_import_helpers.py"
    ).read_text(encoding="utf-8")

    assert "BILL_MODEL_SRC" in helper_text
    assert "Path(__file__).resolve()" in helper_text
    assert "/Users/renyc/" not in helper_text


def test_gitignore_covers_local_environment_and_runtime_data():
    """本机环境文件和 Docker 运行数据不能进入待提交列表。"""
    ignore_text = (_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "backend/env/.env.dev" in ignore_text
    assert "backend/env/.env.prod" in ignore_text
    assert "docker/postgres/data/" in ignore_text


@pytest.mark.parametrize(
    "relative_path",
    [
        "backend/app/config/setting.py",
        "backend/main.py",
        "backend/app/utils/console.py",
        "backend/app/api/v1/module_platform/email/model.py",
        "backend/app/api/v1/module_platform/email/service.py",
        "backend/app/api/v1/module_platform/order/service.py",
        "backend/app/api/v1/module_platform/invoice/pdf_helper.py",
        "frontend/web/src/views/dashboard/screen/modules/ScreenHeader.vue",
        "frontend/web/src/views/fastlink/tutorial/index.vue",
        "frontend/web/src/views/module_platform/email/index.vue",
        "frontend/web/.env.production",
        "backend/env/.env.dev.example",
        "backend/env/.env.prod.example",
        ".dockerignore",
        "docker/nginx/nginx.conf",
        "deploy.bat",
        "backend/templates/invoice/oss_license.jinja2",
        "CONTRIBUTING.md",
        "frontend/web/build/vitePluginStart.ts",
    ],
)
def test_runtime_files_do_not_expose_legacy_brand(relative_path):
    """运行时配置和用户可见文案不能继续暴露旧品牌或旧服务域名。"""
    text = (_ROOT / relative_path).read_text(encoding="utf-8")

    assert "FastapiAdmin" not in text
    assert "fastapiadmin.com" not in text


@pytest.mark.parametrize(
    "relative_path",
    [
        "backend/scripts/_bill_import_helpers.py",
        "backend/app/api/v1/module_semantic/retrieval/eval_bill.py",
    ],
)
def test_runtime_sources_do_not_embed_developer_machine_paths(relative_path):
    """运行时代码不能写死个人开发机路径。"""
    text = (_ROOT / relative_path).read_text(encoding="utf-8")

    assert "/Users/renyc/" not in text


def test_frontend_package_metadata_uses_current_repository():
    """前端包元数据不能包含私有仓库地址，且应声明项目许可证。"""
    package_text = (_ROOT / "frontend" / "web" / "package.json").read_text(
        encoding="utf-8"
    )

    assert '"name": "codataadmin"' in package_text
    assert "internal.git" not in package_text.lower()
    assert '"license": "GPL-3.0-only"' in package_text


def test_production_seed_requires_operator_supplied_admin_password():
    from app.common.enums import EnvironmentEnum
    from app.scripts.initialize import InitializeData
    from app.utils.hash_bcrpy_util import PwdUtil

    from app.config.setting import settings

    original_environment = settings.ENVIRONMENT
    original_password = settings.INITIAL_ADMIN_PASSWORD
    try:
        settings.ENVIRONMENT = EnvironmentEnum.PROD
        settings.INITIAL_ADMIN_PASSWORD = "long-random-bootstrap-password"
        users = InitializeData._prepare_seed_data(
            "sys_user",
            [
                {"username": "super", "password": "known-hash"},
                {"username": "admin", "password": "known-hash"},
            ],
        )
        assert [user["username"] for user in users] == ["super"]
        assert PwdUtil.verify_password(
            settings.INITIAL_ADMIN_PASSWORD, users[0]["password"]
        )
        assert not PwdUtil.verify_password("123456", users[0]["password"])
        assert InitializeData._prepare_seed_data(
            "sys_user_roles",
            [{"user_id": 1, "role_id": 1}, {"user_id": 2, "role_id": 2}],
        ) == [{"user_id": 1, "role_id": 1}]
        assert InitializeData._prepare_seed_data(
            "platform_user_tenant",
            [{"user_id": 1, "tenant_id": 1}, {"user_id": 2, "tenant_id": 1}],
        ) == [{"user_id": 1, "tenant_id": 1}]

        settings.INITIAL_ADMIN_PASSWORD = "short"
        with pytest.raises(ValueError, match="INITIAL_ADMIN_PASSWORD"):
            InitializeData._prepare_seed_data(
                "sys_user", [{"username": "super", "password": "known-hash"}]
            )
    finally:
        settings.ENVIRONMENT = original_environment
        settings.INITIAL_ADMIN_PASSWORD = original_password


def test_current_seed_data_does_not_expose_legacy_brand_or_domain():
    """首次部署导入的 JSON 种子不能继续带旧品牌、旧域名或上游仓库。"""
    seed_dir = _BACKEND / "app" / "scripts" / "data"
    seed_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(seed_dir.glob("*.json"))
    )

    assert "FastapiAdmin" not in seed_text
    assert "fastapiadmin.com" not in seed_text
    assert "service.fastapiadmin.com" not in seed_text
    assert "github.com/fastapi-admin/fastapi-admin" not in seed_text


def test_default_nginx_does_not_require_untracked_tls_certificates():
    """默认 Docker 入口应能在没有证书文件时启动，并代理后端静态资源。"""
    nginx_text = (_ROOT / "docker" / "nginx" / "nginx.conf").read_text(
        encoding="utf-8"
    )
    compose_text = (_ROOT / "docker" / "docker-compose-example.yaml").read_text(
        encoding="utf-8"
    )

    assert "listen 443 ssl" not in nginx_text
    assert "ssl_certificate" not in nginx_text
    assert 'location /static/' in nginx_text
    assert '${HTTPS_PORT:-443}:443' not in compose_text
    assert "./nginx/ssl:/etc/nginx/ssl:ro" not in compose_text


def test_mysql_first_bootstrap_mount_is_present_without_old_dump():
    """MySQL entrypoint 只挂一次性空库脚本，不恢复过期 dump。"""
    services = _docker_compose()["services"]
    mysql_volumes = services["mysql"]["volumes"]

    assert "./mysql/init:/docker-entrypoint-initdb.d:ro" in mysql_volumes

    init_dir = _ROOT / "docker" / "mysql" / "init"
    scripts = {path.name for path in init_dir.iterdir() if path.is_file()}
    assert "01-bootstrap.sh" in scripts

    init_text = "\n".join(path.read_text(encoding="utf-8") for path in init_dir.iterdir() if path.is_file())
    assert "fastapiadmin-2026-06-21_180728-dump.sql" not in init_text


def test_missing_pgvector_breaks_router_registration():
    """★ 固化「缺 pgvector = 全站起不来」，防止有人误以为它只是可选降级。

    ★ 必须在**子进程**里验证：改 `builtins.__import__` 或删 `sys.modules` 都会污染
    同进程的其它测试（且 `importlib.import_module` 根本不走 `builtins.__import__`，
    第一版就是这么写错的 —— 测试红了才发现验证手法本身无效）。
    子进程用 `meta_path` finder 拦截，干净且可靠。

    若将来给 import 链加了 try/except 降级（合理的加固），这条会红 ——
    那时应更新它并把 pgvector 从"硬依赖"改为"可选"。在此之前它提醒读者：这个包不能省。
    """
    import subprocess
    import textwrap

    script = textwrap.dedent("""
        import sys

        class Blocker:
            def find_module(self, name, path=None):
                return self if name.split(".")[0] == "pgvector" else None
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "pgvector":
                    raise ImportError("No module named 'pgvector'")
                return None

        sys.meta_path.insert(0, Blocker())
        try:
            from app.api.v1.module_semantic import semantic_router  # noqa: F401
        except ImportError:
            print("IMPORT_ERROR")
        else:
            print("IMPORTED_OK")
    """)

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_BACKEND, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "ENVIRONMENT": "dev", "PYTHONPATH": str(_BACKEND)},
    )

    assert "IMPORT_ERROR" in proc.stdout, (
        f"缺 pgvector 时语义路由竟能注册 —— 若是有意加了降级，请更新本测试。\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr[-400:]!r}"
    )


# ---------------------------------------------------------------- 配置契约


def _example_values(path: Path) -> dict[str, str]:
    """解析 env example 的 KEY = VALUE（剥注释与引号）。"""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        val = raw.split("#", 1)[0].strip().strip('"').strip("'")
        out[key.strip()] = val
    return out


@pytest.mark.parametrize("example", [".env.dev.example", ".env.prod.example"])
def test_sem_query_dialect_is_documented_in_env_examples(example):
    values = _example_values(_BACKEND / "env" / example)
    assert values["SEM_QUERY_DIALECT"] == "starrocks"


# 安全/正确性敏感的配置：example 的值必须与代码默认值一致。
# 这些项写错不会报错，只会静默改变行为 —— 正是最需要钉住的一类。
_SAFETY_CRITICAL = [
    # 0.65 会让无关问法召回付费率, gold set 实测拒答准确率 100%→0%(setting.py:245-248)
    "SEM_RETRIEVAL_VECTOR_THRESHOLD",
    "SEM_QUERY_MAX_ROWS",
    "SEM_QUERY_TIMEOUT",
]


@pytest.mark.parametrize("example", [".env.dev.example", ".env.prod.example"])
@pytest.mark.parametrize("key", _SAFETY_CRITICAL)
def test_env_example_matches_safety_critical_default(example, key):
    """★★ env example 的安全敏感项必须与 setting.py 默认值一致。

    真实踩到：两个 example 都写 `SEM_RETRIEVAL_VECTOR_THRESHOLD = 0.65`，而代码默认是
    0.40，且该处注释明确写着 0.65 会让拒答准确率 100%→0%（gold set 实测）。
    复制 example 是**正常部署路径**，所以部署者会稳定地继承那个灾难值。

    example 与默认值漂移这一类缺陷不会有任何报错 —— 只会让线上行为悄悄变错。
    """
    from app.config.setting import settings

    values = _example_values(_BACKEND / "env" / example)
    if key not in values:
        pytest.skip(f"{example} 未声明 {key}")

    expected = getattr(settings, key)
    actual = values[key]
    # 数值型按值比较，避免 "0.40" vs "0.4" 的字面差异误报
    if isinstance(expected, bool):
        assert actual.lower() == str(expected).lower(), f"{example} 的 {key} 与默认值不符"
    elif isinstance(expected, (int, float)):
        assert float(actual) == pytest.approx(float(expected)), (
            f"{example} 的 {key}={actual} 与代码默认值 {expected} 不符 —— "
            f"复制 example 是正常部署路径，漂移会静默改变线上行为"
        )
    else:
        assert actual == str(expected), f"{example} 的 {key} 与默认值不符"
