"""语义检索副库（P3）。

PostgreSQL 派生检索副本：别名精确匹配 + 全文（jieba + tsvector）+ 向量（pgvector）三路同库。
不是事实源 —— MySQL 保持权威，本副库由 Release 快照编译而来，可随时 rebuild。
设计：docs/superpowers/specs/2026-07-31-semantic-retrieval-pg-design.md
"""

from .controller import RetrievalRouter

__all__ = ["RetrievalRouter"]
