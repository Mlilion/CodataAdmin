"""MCP server：把本平台的语义检索/召回/取数暴露给外部 agent。

架构：
    Codata (agent, MCP client) ──MCP 协议──▶ CodataAdmin (MCP server)
                                              ├─ search_semantic  找到哪个
                                              ├─ get_model_context 召回权威口径
                                              └─ query_indicator   取数

`retrieval/tools.py` 是实现体，本包是协议层（FastMCP Streamable HTTP）。
"""

from .server import mcp, mcp_http_app

__all__ = ["mcp", "mcp_http_app"]

# ★ 刻意不在此 re-export `McpTokenRouter`：本 __init__ 会 import server(fastmcp)，
# 而 token 管理是**纯 REST** 的东西。re-export 会让整套 REST API 的装配路径
# 依赖 fastmcp 导入成功 —— MCP 协议层出问题就该只影响 /mcp，不该连管理面一起挂。
# 故 `module_semantic/__init__.py` 直接 `from .mcp.controller import ...`。
