"""语义平台的定时任务必须真的被注册（参考方案 §5.2 第三个触发点）。

## 缺口

`scheduled_rebuild`（retrieval/indexer.py）与 `scheduled_backfill`（retrieval/embedding.py）
的 docstring 都写着"供任务管理页把 cronjob 的 `func` 指向本函数路径"。**该机制不存在**：

- cron 的 `func` 列是 `Text`、注释写着"代码块"（module_task/cronjob/node/model.py:22）
- `_task_wrapper`（core/ap_scheduler.py:615）对它 `exec()` 并要求定义 `handler(*args, **kwargs)`
- 所以粘一个点分路径进去只会得到 `ValueError: 代码块必须定义 handler 函数`
- 且 `_task_wrapper` 是**同步**的（threadpool），async 入口还需自己搭事件循环

结果：索引兜底重建与向量补齐**从未自动跑过**，只能靠人手点
`POST /semantic/retrieval/rebuild` 与 `/embed`。

## 为什么走 `_register_system_jobs` 而不是 seed `task_node`

`_register_system_jobs` 已经在注册一个 async 函数（`cleanup_operation_log`），走
`AsyncIOExecutor`（ap_scheduler.py:78）原生 await 协程、在主事件循环里执行，
单例 session factory 与连接池完全正常。而 `task_node` 那条路要用 `asyncio.run` 跨事件
循环重建连接池（实测有 `Future attached to a different loop` 风险），且存量库
`task_node` 有数据时种子会被整体跳过 —— 对已部署环境天然失效。
"""

import inspect

import pytest

_EXPECTED_JOBS = {
    "semantic_retrieval_rebuild": "语义检索索引兜底重建",
    "semantic_embedding_backfill": "语义向量补齐",
    "semantic_querylog_purge": "语义 query 日志清理",
}


@pytest.fixture
def registered_specs():
    """取系统级任务的注册清单（纯数据，不真的启调度器）。"""
    from app.core.ap_scheduler import SchedulerUtil

    return {spec["id"]: spec for spec in SchedulerUtil.system_job_specs()}


def test_semantic_jobs_are_registered(registered_specs):
    """★★ 三个语义定时任务都必须在系统任务清单里。

    少了任何一个，对应能力就退化为"只能人手点"：索引漂移无人修复、
    向量永远待生成、query 日志无界增长。
    """
    missing = sorted(set(_EXPECTED_JOBS) - set(registered_specs))

    assert not missing, f"这些语义定时任务未注册: {missing}"


@pytest.mark.parametrize("job_id", sorted(_EXPECTED_JOBS))
def test_job_func_is_zero_arg_coroutine(registered_specs, job_id):
    """★★ 每个入口都必须是**零参协程**。

    这是本文件最有价值的一条：`AsyncIOExecutor` 直接 await 它，参数由调度器传空。
    若有人日后把它改成需要参数、或改成同步函数，问题只会在**定时触发那一刻**暴露
    （半夜 4 点，且失败被入口自己的 try/except 吞掉）—— 这里提前钉死。
    """
    func = registered_specs[job_id]["func"]

    assert inspect.iscoroutinefunction(func), f"{job_id} 的入口不是协程函数"
    required = [
        p for p in inspect.signature(func).parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]
    assert not required, f"{job_id} 的入口有必填参数 {[p.name for p in required]}，调度器不会传"


@pytest.mark.parametrize("job_id", sorted(_EXPECTED_JOBS))
def test_job_has_trigger_and_stable_name(registered_specs, job_id):
    """★ 每个任务都要有 trigger 与人类可读的名字（运维在任务页要认得出来）。"""
    spec = registered_specs[job_id]

    assert spec.get("trigger") is not None, f"{job_id} 缺 trigger"
    assert spec.get("name") == _EXPECTED_JOBS[job_id]


@pytest.mark.asyncio
async def test_scheduled_rebuild_returns_dict_when_disabled(monkeypatch):
    """★ 关闭检索开关时应返回 dict 而非抛异常。

    定时任务抛异常会被调度器标记失败、甚至停掉该 job —— 而漏一次兜底重建
    远没有"调度器把任务停了"严重。所以入口必须自己吞掉。
    """
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEMANTIC_RETRIEVAL_ENABLE", False)

    from app.api.v1.module_semantic.retrieval.indexer import scheduled_rebuild

    result = await scheduled_rebuild()

    assert isinstance(result, dict)
    assert result.get("skipped") is True


@pytest.mark.asyncio
async def test_scheduled_purge_swallows_errors(monkeypatch):
    """★ 日志清理入口在 DB 出问题时也不得抛 —— 同上，别把调度器搞停。"""
    from app.api.v1.module_semantic.querylog import service as svc_mod

    class _Boom:
        def __call__(self, *a, **k):
            raise RuntimeError("DB 挂了")

    monkeypatch.setattr(svc_mod, "async_db_session", _Boom())

    result = await svc_mod.scheduled_purge()

    assert isinstance(result, dict)
    assert "error" in result


def test_docstrings_do_not_advertise_nonexistent_cron_mechanism():
    """★ 两个入口的 docstring 不得再写"把 cronjob 的 func 指向本函数路径"。

    那个机制不存在（见模块顶部说明），留着会把下一个人引进 `exec()` 代码块的坑里 ——
    这类**误导性文档**比没有文档更贵。
    """
    from app.api.v1.module_semantic.retrieval.embedding import scheduled_backfill
    from app.api.v1.module_semantic.retrieval.indexer import scheduled_rebuild

    for func in (scheduled_rebuild, scheduled_backfill):
        doc = func.__doc__ or ""
        assert "指向本函数路径" not in doc, (
            f"{func.__name__} 的 docstring 仍在宣传不存在的 cron 注册方式"
        )
