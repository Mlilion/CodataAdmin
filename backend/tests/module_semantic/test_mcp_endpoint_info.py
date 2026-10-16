"""MCP 接入信息端点（页面上要能抄到「填哪个地址」）。

## 缺口

「MCP 令牌」页此前只给出 `Authorization: Bearer <token>`，**从不给 URL**。
于是拿到令牌的人知道钥匙是什么，却不知道门在哪 —— 得回去翻
`docs/semantic-mcp.md`，而那里写的是占位符 `http://<host>:<port>/mcp`，
仍然答不出"我这套部署到底填什么"。这与本轮修的那批「系统摆出一个入口、
照着做却做不成」是同一类缺陷。

## 本文件固化的判据

1. **URL 由请求推导**，不写死 —— 同一份代码在 dev/测试/生产被访问时答案不同
2. **`SEM_MCP_PUBLIC_URL` 能覆盖推导值** —— 反代/网关后面，请求里的 host 是内网地址
3. **`/mcp` 不在 `ROOT_PATH` 下** —— 它是另一套协议，不带 `/api/v1` 前缀
4. **端点没挂载时要说实话** —— 给一个不服务的地址比不给更坏
5. 该端点**不返回任何令牌明文或哈希**（结构上就没有该字段）
"""

import pytest

from tests.conftest import assert_route


@pytest.fixture(autouse=True)
def _reset_public_url(monkeypatch):
    """默认清掉覆盖项，让用例各自决定。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_PUBLIC_URL", "", raising=False)


def _get(test_client, auth_headers):
    resp = test_client.get("/semantic/mcp/token/endpoint", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------- URL 推导


def test_endpoint_url_is_derived_from_request(test_client, auth_headers):
    """★★ URL 从请求推导 —— 写死会让至少一种环境拿到错地址。"""
    data = _get(test_client, auth_headers)

    assert data["url"], "没给 URL —— 页面上仍然答不出'填哪个地址'"
    assert data["url"].endswith("/mcp"), f"MCP 端点路径应是 /mcp，实际 {data['url']}"
    assert data["url"].startswith("http"), "URL 必须带 scheme，否则不能直接粘进 client 配置"


def test_endpoint_url_does_not_carry_api_prefix(test_client, auth_headers):
    """★★ `/mcp` 挂在根上，不在 ROOT_PATH 下。

    ★ dev 实测：带上 `/api/v1` 前缀**恰好也能连上**（Starlette 的 `get_route_path`
    会先剥掉 `root_path` 再匹配路由）。但那是巧合不是契约 —— 反代改写路径时
    就不成立。所以给出的地址必须是不带前缀的形式，不能抄走一个碰巧能用的。
    """
    from app.config.setting import settings

    prefix = (settings.ROOT_PATH or "").rstrip("/")
    data = _get(test_client, auth_headers)

    if prefix:
        assert prefix not in data["url"], (
            f"URL 里混进了 REST 前缀 {prefix}：{data['url']} —— 这个地址会 404"
        )


def test_public_url_override_wins(test_client, auth_headers, monkeypatch):
    """★★ 反代后面必须能覆盖：请求里的 host 是内网地址，抄出去连不上。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_PUBLIC_URL", "https://admin.example.com/mcp")
    data = _get(test_client, auth_headers)

    assert data["url"] == "https://admin.example.com/mcp"
    assert data["source"] == "config", "覆盖生效时要说明来源，否则没人知道为何与浏览器地址不同"


def test_public_url_override_gets_mcp_path_appended(test_client, auth_headers, monkeypatch):
    """只配了站点根地址也要能用 —— 逼人记住补 `/mcp` 是没必要的坑。"""
    from app.config.setting import settings

    monkeypatch.setattr(settings, "SEM_MCP_PUBLIC_URL", "https://admin.example.com")
    data = _get(test_client, auth_headers)

    assert data["url"] == "https://admin.example.com/mcp"


def test_derived_source_is_labelled(test_client, auth_headers):
    """未覆盖时来源标 request —— 让人知道"这是猜的，反代后面请自行核对"。"""
    data = _get(test_client, auth_headers)
    assert data["source"] == "request"


# ---------------------------------------------------------------- 说实话


def test_reports_whether_endpoint_is_actually_mounted(test_client, auth_headers):
    """★★ 给地址的同时要说它到底在不在服务。

    端点可能因 `SEM_MCP_ENABLE=false` 或安全门禁（非 dev + 无 token）而未挂载。
    此时给出一个 URL 却不说明，人会拿去配 client 然后收到 404，
    并把原因归到自己身上。
    """
    data = _get(test_client, auth_headers)

    assert "mounted" in data, "没说端点在不在服务 —— 一个不服务的地址比不给更坏"
    assert isinstance(data["mounted"], bool)
    # 测试环境按真实装配结果断言，不写死 True/False
    from app.init_app import app_has_mcp_route  # noqa: PLC0415

    assert data["mounted"] is app_has_mcp_route(test_client.app), "mounted 与真实路由表不一致"


def test_transport_and_header_hints_are_present(test_client, auth_headers):
    """把 client 配置需要的三件套一次给全：URL / 传输方式 / 鉴权头。"""
    data = _get(test_client, auth_headers)

    assert data["transport"], "没给传输方式，MCP client 里选哪一项要靠猜"
    assert "Bearer" in data["auth_header"], "鉴权头示例里要有 Bearer"


def test_no_token_material_is_leaked(test_client, auth_headers):
    """★ 该端点不碰令牌本体 —— 结构上不该出现明文或哈希。"""
    data = _get(test_client, auth_headers)

    assert "token" not in data, "接入信息端点不该返回任何令牌"
    assert "token_hash" not in data
    # auth_header 是示例，里面必须是占位符而不是某个真令牌
    assert "sk_" not in data["auth_header"], "鉴权头示例里混进了真令牌前缀"


# ---------------------------------------------------------------- 权限


def test_requires_token_permission(test_client):
    """★ 沿用 `module_semantic:mcp:token`：知道端点在哪属于接入配置的一部分。"""
    assert_route(test_client, "get", "/semantic/mcp/token/endpoint", expected_status=401)
