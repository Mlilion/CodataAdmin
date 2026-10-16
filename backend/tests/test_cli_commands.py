"""CLI 子命令契约。

命令是**部署清单的可执行部分** —— 清单里写"跑 python main.py init-retrieval"，
而命令若被改名/改签名/丢失，清单就静默失效（没人会为部署文档跑测试）。
本文件把清单依赖的命令钉住。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from main import fastapiadmin_cli

runner = CliRunner()
BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_init_retrieval_command_exists():
    """★ 部署清单依赖这个命令名，不得随意改。"""
    result = runner.invoke(fastapiadmin_cli, ["init-retrieval", "--help"])

    assert result.exit_code == 0
    assert "--env" in result.stdout


def test_run_env_option_loads_env_file_before_settings():
    """`run --env=dev` 必须在 settings 首次实例化前生效。

    实际踩到：main.py 顶部 import logger/settings，`run()` 里再设置 ENVIRONMENT
    已经太晚，服务按默认值启动，导致 .env.dev 里的 SEM_RAW_SQL_ENABLE=true
    和 SEM_QUERY_* 没进运行进程。
    """
    code = """
import os
from typer.testing import CliRunner

os.environ.pop("ENVIRONMENT", None)
import main

def fake_run(**_kwargs):
    from app.config.setting import settings
    print(f"env={settings.ENVIRONMENT.value}")
    print(f"raw={settings.SEM_RAW_SQL_ENABLE}")
    print(f"query_host={settings.SEM_QUERY_HOST}")

main.uvicorn.run = fake_run
result = CliRunner().invoke(main.fastapiadmin_cli, ["run", "--env=dev"])
print(f"exit={result.exit_code}")
print(result.output)
if result.exception:
    print(repr(result.exception))
"""
    env = os.environ.copy()
    env.pop("ENVIRONMENT", None)
    env["PYTHONPATH"] = str(BACKEND_DIR)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "exit=0" in out, out
    assert "env=dev" in out, out
    assert "raw=True" in out, out
    assert "query_host=127.0.0.1" in out, out


def test_init_retrieval_skips_gracefully_when_disabled(monkeypatch):
    """★★ 未启用检索时应跳过并退出 0 —— 这是**正常部署路径**，不是错误。

    若返回非零，未启用检索的部署会在清单这一步"失败"，逼运维去查一个不存在的问题；
    CI/部署脚本里 `set -e` 还会直接中断后续步骤。
    """
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)

    result = runner.invoke(fastapiadmin_cli, ["init-retrieval", "--env=dev"])

    assert result.exit_code == 0, f"未启用时不该失败: {result.stdout}"
    assert "跳过" in result.stdout


def test_init_retrieval_reports_actionable_error_on_bad_connection(monkeypatch):
    """★ 连不上时要给出可行动的信息（host/port/db），而不是抛栈。

    部署现场最常见的失败就是配错连接项。错误消息里必须带上它实际用的连接目标，
    否则运维只能靠猜 —— 这与 executor / search 那些"面向用户的错误要可行动"同一原则。
    """
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_PG_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "SEM_PG_PORT", 1)  # 必然连不上
    monkeypatch.setattr(settings, "SEM_PG_DB", "nope")

    # 错误走 stderr（typer.echo(err=True)）。click 8.1 的 CliRunner 默认把 stderr
    # 混进 output，故读 result.output 而非 result.stdout。
    result = runner.invoke(fastapiadmin_cli, ["init-retrieval", "--env=dev"])

    assert result.exit_code == 1
    output = result.output or ""
    assert "nope" in output or "127.0.0.1" in output, (
        f"错误消息没带连接目标，运维无从下手: {output[:300]}"
    )


@pytest.mark.parametrize("name", ["run", "revision", "upgrade", "init-db", "init-retrieval"])
def test_deploy_checklist_commands_all_present(name):
    """★ 部署清单引用的全部命令都存在（一处改名就红）。"""
    result = runner.invoke(fastapiadmin_cli, [name, "--help"])

    assert result.exit_code == 0, f"命令 {name} 不存在或已改名"
