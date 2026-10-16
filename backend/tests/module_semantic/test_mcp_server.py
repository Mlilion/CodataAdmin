"""MCP server（Codata agent 经 MCP 调本平台的检索/召回/取数）。

架构：
    Codata (agent, MCP client) ──MCP 协议──▶ CodataAdmin (MCP server)
                                              ├─ search_semantic  找到哪个
                                              ├─ get_model_context 召回权威口径
                                              └─ query_indicator   取数

P4-4 的三个函数是实现体，本模块是协议层。用 FastMCP 的 Streamable HTTP
（stateless，多 worker 安全 —— 与 DataSage 的做法一致）。

★ MCP 的 description 就是 agent 唯一能看到的说明。它不是注释，是接口契约的一部分：
写不清"什么时候用、needs_clarify 该怎么处理"，接上了也会被用错。
"""

import json

from app.api.v1.module_semantic.mcp import server as mcp_server

# ---------------------------------------------------------------- 注册与契约


async def _tool_map() -> dict:
    """name → tool（fastmcp 3.x 的 list_tools 返回列表）。"""
    return {t.name: t for t in await mcp_server.mcp.list_tools()}


async def test_all_tools_registered():
    """四个工具都注册进 MCP（list_models 是发现入口，见「模型可发现性」一节）。"""
    tools = await _tool_map()

    assert {
        "list_models", "search_semantic", "get_model_context", "query_indicator",
    } <= set(tools)


async def test_instructions_mention_discovery_entry():
    """★ instructions 是 agent 看到的**流程说明** —— 必须把入口工具写进去。

    否则 agent 从 search_semantic 起步，遇到需要 model_id 的工具时无从下手。
    """
    assert "list_models" in (mcp_server.mcp.instructions or "")


async def test_every_tool_has_substantial_description():
    """★ description 是 agent 唯一能看到的说明 —— 太短它就会用错。"""
    tools = await _tool_map()

    for name, tool in tools.items():
        desc = tool.description or ""
        assert len(desc) > 100, f"{name} 的 description 太短({len(desc)} 字), agent 用不好"


async def test_search_description_warns_about_flags():
    """检索工具必须说明 needs_clarify / not_buildable 该怎么处理。"""
    tools = await _tool_map()
    desc = tools["search_semantic"].description

    assert "needs_clarify" in desc
    assert "not_buildable" in desc


async def test_query_description_forbids_writing_sql():
    """★ 取数工具必须明确"你不写 SQL" —— 否则 agent 会自己编。"""
    tools = await _tool_map()
    desc = tools["query_indicator"].description

    assert "SQL" in desc
    assert "口径" in desc


# ---------------------------------------------------------------- 调用行为


async def test_tools_return_json_string(monkeypatch):
    """MCP tool 返回 JSON 字符串（协议要求文本内容）。"""
    async def _fake(*a, **kw):
        return {"ok": True, "items": [{"code": "sub_revenue", "score": 0.97}]}

    monkeypatch.setattr(mcp_server, "_search", _fake)

    out = await mcp_server.search_semantic(query="订阅收入")

    assert isinstance(out, str)
    assert json.loads(out)["items"][0]["code"] == "sub_revenue"


async def test_tool_failure_returns_structured_json(monkeypatch):
    """★ 后端故障也返回 JSON 而非抛异常 —— MCP client 拿到栈没法反思。"""
    async def _boom(*a, **kw):
        raise RuntimeError("PG 挂了")

    monkeypatch.setattr(mcp_server, "_search", _boom)

    out = await mcp_server.search_semantic(query="订阅收入")
    payload = json.loads(out)

    assert payload["ok"] is False
    assert "PG 挂了" in payload["error"]


async def test_query_passes_hints_through(monkeypatch):
    """检索标记透传到取数 —— 草稿/跨域指标才能拿到可行动的说明而非"不存在"。"""
    captured = {}

    async def _fake(**kw):
        captured.update(kw)
        return {"ok": False, "errors": []}

    monkeypatch.setattr(mcp_server, "_query", _fake)

    await mcp_server.query_indicator(
        model_id=2, indicator="renewal_rate", start="2026-06-01", end="2026-06-30",
        needs_clarify=True, hint_note="口径待确认",
    )

    assert captured["needs_clarify"] is True
    assert captured["hint_note"] == "口径待确认"


async def test_search_semantic_accepts_stringified_numeric_ids(monkeypatch):
    """★ 字符串化的数字 model_id / top_k 应在 MCP 边界被归一化。"""
    captured = {}

    async def _fake(query, model_id=None, top_k=5, node_types=None):
        captured.update(
            {"query": query, "model_id": model_id, "top_k": top_k, "node_types": node_types}
        )
        return {"ok": True, "items": []}

    monkeypatch.setattr(mcp_server, "_search", _fake)

    out = await mcp_server.search_semantic(query="收入", model_id="1", top_k="3")

    assert json.loads(out)["ok"] is True
    assert captured["model_id"] == 1
    assert captured["top_k"] == 3


async def test_get_model_context_accepts_stringified_numeric_model_id(monkeypatch):
    """★ get_model_context 入口也要兼容数字字符串。"""
    captured = {}

    async def _fake(model_id, indicator_codes=None, dimension_codes=None, knowledge_keys=None):
        captured.update(
            {
                "model_id": model_id,
                "indicator_codes": indicator_codes,
                "dimension_codes": dimension_codes,
                "knowledge_keys": knowledge_keys,
            }
        )
        return {"ok": True, "model": {"code": "demo", "version": 1}}

    monkeypatch.setattr(mcp_server, "_context", _fake)

    out = await mcp_server.get_model_context(model_id="1", indicator_codes=["total_revenue"])

    assert json.loads(out)["ok"] is True
    assert captured["model_id"] == 1
    assert captured["indicator_codes"] == ["total_revenue"]


async def test_query_indicator_accepts_stringified_numeric_ids(monkeypatch):
    """★ 取数入口也要兼容数字字符串，避免桌面端偶发串化直接失败。"""
    captured = {}

    async def _fake(**kw):
        captured.update(kw)
        return {
            "ok": True,
            "rows": [],
            "row_count": 0,
            "executed_sql": "SELECT 1",
            "truncated": False,
            "caliber": {},
            "knowledge": [],
            "errors": [],
        }

    monkeypatch.setattr(mcp_server, "_query", _fake)

    out = await mcp_server.query_indicator(
        model_id="1",
        indicator="total_revenue",
        start="2026-01-01",
        end="2026-12-31",
        max_rows="4",
    )

    assert json.loads(out)["ok"] is True
    assert captured["model_id"] == 1
    assert captured["max_rows"] == 4


async def test_mcp_tool_schemas_allow_stringified_numeric_ids():
    """★ 注册到 MCP 的 schema 也必须允许数字字符串，客户端才不会前置拦截。"""
    tools = await _tool_map()

    search_schema = tools["search_semantic"].parameters
    query_schema = tools["query_indicator"].parameters
    context_schema = tools["get_model_context"].parameters

    assert any(opt.get("type") == "string" for opt in search_schema["properties"]["model_id"]["anyOf"])
    assert any(opt.get("type") == "string" for opt in search_schema["properties"]["top_k"]["anyOf"])
    assert any(opt.get("type") == "string" for opt in context_schema["properties"]["model_id"]["anyOf"])
    assert any(opt.get("type") == "string" for opt in query_schema["properties"]["model_id"]["anyOf"])
    assert any(opt.get("type") == "string" for opt in query_schema["properties"]["max_rows"]["anyOf"])


# ---------------------------------------------------------------- HTTP 挂载


def test_http_app_exposed():
    """暴露 Streamable HTTP app 供挂进 FastAPI。"""
    assert mcp_server.mcp_http_app is not None


def test_mounted_on_main_app():
    """★ MCP 端点真的挂上了 —— 没挂等于 Codata 连不上。"""
    import inspect

    from app import init_app

    source = inspect.getsource(init_app)
    assert "mcp" in source.lower(), "init_app 未挂载 MCP"


def _mcp_route_paths(app) -> set[str]:
    """MCP 走 Starlette Route 注册（非 mount，原因见 init_app.register_mcp）。"""
    return {
        getattr(r, "path", None) for r in app.routes
        if str(getattr(r, "path", "")).startswith("/mcp")
    }


def test_mcp_mounted_on_real_app():
    """★ /mcp 与 /mcp/ 都注册了 —— 没注册等于 Codata 连不上。

    不走 _api_client：它是 session 级 fixture，app 实例可能早于本用例创建。
    直接建一个 app 检查路由表，才是对"挂没挂上"的可靠断言。
    两个路径都收：MCP client 配 /mcp 或 /mcp/ 都能连（实测 307 重定向会断会话）。
    """
    from main import create_app

    paths = _mcp_route_paths(create_app())

    assert {"/mcp", "/mcp/"} <= paths, f"MCP 路由不全: {paths}"


def test_mcp_mount_can_be_disabled(monkeypatch):
    """总开关关闭时不注册（不需要 MCP 的部署不该暴露端点）。"""
    from app.config.setting import settings
    from main import create_app

    monkeypatch.setattr(settings, "SEM_MCP_ENABLE", False)

    assert _mcp_route_paths(create_app()) == set()


# ---------------------------------------------------------------- 鉴权


async def test_auth_required_when_token_configured(monkeypatch):
    """★ 配了 token 就必须校验 —— 语义口径与数仓查询不能匿名开放。"""
    from app.api.v1.module_semantic.mcp.auth import verify_mcp_token
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", "secret-token")

    assert verify_mcp_token("secret-token") is True
    assert verify_mcp_token("wrong") is False
    assert verify_mcp_token(None) is False


async def test_auth_open_when_token_empty(monkeypatch):
    """未配 token 则不鉴权（本地开发便利），但要有明确日志警告。"""
    from app.api.v1.module_semantic.mcp.auth import verify_mcp_token
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", "")

    assert verify_mcp_token(None) is True


def test_token_comparison_is_constant_time():
    """★ token 比较用恒定时间函数，防时序侧信道。"""
    import inspect

    from app.api.v1.module_semantic.mcp import auth

    source = inspect.getsource(auth)
    assert "compare_digest" in source, "token 比较应使用 secrets.compare_digest"


# ---------------------------------------------------------------- 模型可发现性


async def test_list_models_tool_registered():
    """★★ 必须有工具能**发现** model_id —— 否则接上来第一步就卡住。

    真实踩到：三个工具全都要求 model_id 必填，但没有任何工具能列出可用模型。
    端到端验证时只能靠试探 model_id=1,2,3 才知道有哪些 —— MCP client
    没有库表访问权，它试不出来。
    """
    tools = await _tool_map()

    assert "list_models" in tools, (
        f"没有列模型的工具，agent 无法发现 model_id。已注册: {sorted(tools)}"
    )


async def test_list_models_needs_no_arguments():
    """★ 列模型不能要求任何必填参数 —— 它是**入口**，此时 agent 一无所知。"""
    tools = await _tool_map()
    # fastmcp 3.x 的 FunctionTool 上是 `parameters`（不是 MCP wire 的 inputSchema）
    schema = tools["list_models"].parameters or {}

    assert not schema.get("required"), (
        f"list_models 有必填参数 {schema.get('required')}，"
        f"但它是发现入口，调用时 agent 还什么都不知道"
    )
    assert not schema.get("properties"), (
        f"list_models 有参数 {list(schema.get('properties', {}))}，入口工具应零参数"
    )


async def test_list_models_returns_id_and_code(monkeypatch):
    """返回 id + code + name —— id 用于后续调用，code/name 用于让 agent 判断选哪个。"""
    async def _fake():
        return [{"id": 2, "code": "commercial_revenue", "name": "商业化营收",
                 "version": 3, "description": "计费与营收"}]

    monkeypatch.setattr(mcp_server, "_list_released_models", _fake)

    out = json.loads(await mcp_server.list_models())

    assert out["ok"] is True
    assert out["models"][0]["id"] == 2
    assert out["models"][0]["code"] == "commercial_revenue"
    assert out["models"][0]["name"] == "商业化营收"


async def test_list_models_only_released(monkeypatch):
    """★ 只列**已发布**的模型 —— 未发布的没有快照，后续调用必然失败。"""
    import inspect

    src = inspect.getsource(mcp_server._list_released_models)

    assert "sem_model_release" in src or "release" in src.lower(), (
        "列模型时未限定已发布 —— 未发布模型没有快照，agent 选了会失败"
    )


async def test_list_models_failure_returns_structured_json(monkeypatch):
    """库不可达时给结构化错误，不抛异常（MCP 协议层不该炸）。"""
    async def _boom():
        raise RuntimeError("库炸了")

    monkeypatch.setattr(mcp_server, "_list_released_models", _boom)

    out = json.loads(await mcp_server.list_models())

    assert out["ok"] is False
    assert "库炸了" in out.get("error", "")


# ---------------------------------------------------------------- 环境安全门禁


def _mount(monkeypatch, *, environment: str, token: str) -> list[str]:
    """按给定环境/token 调用 register_mcp，返回挂载出的路径列表。"""
    from fastapi import FastAPI

    from app.config.setting import settings
    from app.init_app import register_mcp

    monkeypatch.setattr(settings, "SEM_MCP_ENABLE", True)
    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", token)
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)

    app = FastAPI()
    register_mcp(app)
    return [getattr(r, "path", "") for r in app.router.routes]


def test_prod_refuses_to_mount_unauthenticated_mcp(monkeypatch):
    """★★ prod + 空 token → 拒绝挂载 /mcp。

    默认姿态本来是危险的：`SEM_MCP_ENABLE` 默认 True 而 `SEM_MCP_TOKEN` 默认 ""，
    且 prod.example 也发的是空 token。mcp/auth.py 在空 token 时直接放行，
    所以默认部署等于把 `query_indicator`（任意参数化数仓只读查询）对外裸奔，
    唯一的阻挡是一条 logger.warning。

    ★ 用"拒绝挂载"而非"启动失败"：与 register_mcp 既有原则一致
    （MCP 挂载失败不该让整个应用起不来）。安全的失败方向是端点不存在。
    """
    paths = _mount(monkeypatch, environment="prod", token="")

    assert "/mcp" not in paths, "prod 环境空 token 竟挂载了无鉴权 MCP —— 数仓能力裸奔"


def test_prod_mounts_mcp_when_token_configured(monkeypatch):
    """★ prod + 有 token → 正常挂载（门禁只挡无鉴权，不挡功能）。"""
    paths = _mount(monkeypatch, environment="prod", token="a-strong-random-token")

    assert "/mcp" in paths


def test_dev_still_mounts_without_token(monkeypatch):
    """★ dev + 空 token → 仍挂载。本地开发便利不因安全门禁而回退。

    与仓库既有姿态一致：seed 脚本也是按 ENVIRONMENT 分级（非 dev/test 才拒绝），
    而不是全局关闭功能。
    """
    paths = _mount(monkeypatch, environment="dev", token="")

    assert "/mcp" in paths, "dev 环境不该被门禁挡住"


# ---------------------------------------------------------------- 多 token + 调用方标识


def _set_tokens(monkeypatch, *, single: str = "", multi: str = "") -> None:
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_TOKEN", single)
    monkeypatch.setattr(settings, "SEM_MCP_TOKENS", multi)


def test_multiple_tokens_all_accepted(monkeypatch):
    """★★ 多 token 都放行，未列入的拒绝。

    单一共享 token 的问题：一个字符串对应"所有调用者"，谁调的、该不该调都无法区分；
    且一处泄露就得全量换、所有接入方一起断。
    """
    from app.api.v1.module_semantic.mcp.auth import verify_mcp_token

    _set_tokens(monkeypatch, multi="codata:tok-a,ops:tok-b")

    assert verify_mcp_token("tok-a") is True
    assert verify_mcp_token("tok-b") is True
    assert verify_mcp_token("tok-c") is False
    assert verify_mcp_token(None) is False


def test_identify_caller_returns_configured_name(monkeypatch):
    """★★ token → 调用方名。这是 query 日志能记下"谁在问"的前提。"""
    from app.api.v1.module_semantic.mcp.auth import identify_caller

    _set_tokens(monkeypatch, multi="codata:tok-a,ops:tok-b")

    assert identify_caller("tok-a") == "codata"
    assert identify_caller("tok-b") == "ops"


def test_unknown_token_is_neither_accepted_nor_identified(monkeypatch):
    """★ 未知 token 既不放行、也识别不出调用方（不得回落成某个已知名字）。"""
    from app.api.v1.module_semantic.mcp.auth import identify_caller, verify_mcp_token

    _set_tokens(monkeypatch, multi="codata:tok-a")

    assert verify_mcp_token("bogus") is False
    assert identify_caller("bogus") is None


def test_legacy_single_token_still_works(monkeypatch):
    """★★ 兼容性回归锚：只配旧的 SEM_MCP_TOKEN 时行为必须不变。

    既有部署（.env.dev / prod.example / register_mcp 门禁 / readiness）都依赖那一项 ——
    若新配置项上线后旧项失效，现有部署会在**升级瞬间变成无鉴权**，是安全倒退。
    """
    from app.api.v1.module_semantic.mcp.auth import identify_caller, verify_mcp_token

    _set_tokens(monkeypatch, single="legacy-tok")

    assert verify_mcp_token("legacy-tok") is True
    assert verify_mcp_token("wrong") is False
    assert identify_caller("legacy-tok") == "default"


def test_no_token_configured_still_open(monkeypatch):
    """★ 两项都空 → 仍全放行（dev 便利），调用方记作 anonymous。"""
    from app.api.v1.module_semantic.mcp.auth import identify_caller, verify_mcp_token

    _set_tokens(monkeypatch)

    assert verify_mcp_token(None) is True
    assert identify_caller(None) == "anonymous"


def test_multi_token_comparison_stays_constant_time():
    """★★ 多 token 后不得退化成 `in` 查找。

    `token in tokens_dict` 是哈希查找 —— 会泄露时序。必须遍历候选逐个
    `secrets.compare_digest`。这条防的是"为了写得短而丢掉恒定时间"这类退化。
    """
    import inspect

    from app.api.v1.module_semantic.mcp import auth

    src = inspect.getsource(auth.verify_mcp_token)

    assert "compare_digest" in src, "多 token 校验丢了恒定时间比较"
    assert " in " not in src.replace("for ", "").replace("not in", ""), (
        f"疑似用了哈希查找（会泄露时序）:\n{src}"
    )


def test_prod_gate_accepts_either_token_setting(monkeypatch):
    """★★ 只配 SEM_MCP_TOKENS 时，prod 也应正常挂载 /mcp。

    门禁原判据只看 SEM_MCP_TOKEN —— 若不同步，新配置项在 prod 反而挂不上，
    是个隐性倒退（运维配了多 token 却发现端点消失，且日志说"token 为空"）。
    """
    _set_tokens(monkeypatch, multi="codata:tok-a")
    paths = _mount(monkeypatch, environment="prod", token="")

    assert "/mcp" in paths, "配了 SEM_MCP_TOKENS 却仍被门禁拒绝挂载"
