"""MCP 访问令牌的管理面（纯 REST）。

## 为什么与 `mcp/` 分开成一个包

`mcp/__init__.py` 会 import `server`（fastmcp）—— 那是 MCP **协议层**。
而令牌管理是普通的 REST CRUD，被 `module_semantic/__init__.py` 装配进 API 路由。
若把它放在 `mcp/` 包里，整套 REST API 的装配路径就会依赖 fastmcp 导入成功：
MCP 协议层出任何问题（依赖缺失、版本不兼容）都会连管理面一起挂掉，
而这时**恰恰**最需要管理面能用（比如去吊销一个泄露的 token）。

鉴权判定仍在 `mcp/auth.py`（那是协议层要用的），本包只负责签发/轮换/吊销，
并在写操作后调 `auth.invalidate_token_cache()` 让判定层立刻看到变化。
"""

from .controller import McpTokenRouter

__all__ = ["McpTokenRouter"]
