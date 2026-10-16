"""数仓元数据采集器。

自主连 StarRocks（MySQL 协议，9030），扫 information_schema 采集库/表/列。
仅读 information_schema，参数化查询；同步执行（供 service 在 threadpool 中调用）。
"""

from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.core.exceptions import CustomException
from app.core.logger import logger

# 采集时排除的系统库
_SYSTEM_DBS = {"information_schema", "_statistics_", "sys", "mysql", "performance_schema"}


class StarRocksCollector:
    """给定连接信息，扫 StarRocks information_schema。"""

    def __init__(self, host: str, port: int, username: str, password: str, database: str | None = None, timeout: int = 30) -> None:
        # StarRocks 走 MySQL 协议；database 仅用于建连，采集跨库查 information_schema
        # URL.create 自动转义账号/密码中的 @ : / % 等特殊字符
        self._url = URL.create(
            "mysql+pymysql",
            username=username,
            password=password,
            host=host,
            port=port,
            database=database or "information_schema",
        )
        self._timeout = timeout

    def _engine(self):
        return create_engine(
            self._url,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={"connect_timeout": self._timeout, "read_timeout": self._timeout},
        )

    def test_connection(self) -> bool:
        """连接测试：SELECT 1。失败抛 CustomException。"""
        try:
            eng = self._engine()
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            eng.dispose()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"StarRocks 连接测试失败: {e}")
            raise CustomException(msg=f"连接失败: {e}")

    def _query(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        try:
            eng = self._engine()
            with eng.connect() as conn:
                rows = conn.execute(text(sql), params or {}).mappings().all()
            eng.dispose()
            return [dict(r) for r in rows]
        except CustomException:
            raise
        except Exception as e:  # noqa: BLE001
            raise CustomException(msg=f"StarRocks 采集查询失败: {e}")

    def list_databases(self) -> list[str]:
        rows = self._query("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA")
        return [r["SCHEMA_NAME"] for r in rows if r.get("SCHEMA_NAME") and r["SCHEMA_NAME"] not in _SYSTEM_DBS]

    def list_tables(self, db_name: str) -> list[dict]:
        rows = self._query(
            """
            SELECT TABLE_NAME, TABLE_TYPE, TABLE_ROWS, DATA_LENGTH, TABLE_COMMENT
            FROM information_schema.tables
            WHERE TABLE_SCHEMA = :db
            ORDER BY TABLE_NAME
            """,
            {"db": db_name},
        )
        return [
            {
                "table_name": r.get("TABLE_NAME"),
                "table_type": r.get("TABLE_TYPE"),
                "row_count": r.get("TABLE_ROWS"),
                "data_size": r.get("DATA_LENGTH"),
                "comment": r.get("TABLE_COMMENT"),
            }
            for r in rows
            if r.get("TABLE_NAME")
        ]

    def list_columns_by_db(self, db_name: str) -> dict[str, list[dict]]:
        """一次查询取回整库所有表的列，按表名分组(远程实例网络往返是主要开销,忌逐表查询)。"""
        rows = self._query(
            """
            SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_DEFAULT, IS_NULLABLE,
                   DATA_TYPE, COLUMN_TYPE, COLUMN_KEY, COLUMN_COMMENT
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :db
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            {"db": db_name},
        )
        grouped: dict[str, list[dict]] = {}
        for r in rows:
            tbl = r.get("TABLE_NAME")
            col = r.get("COLUMN_NAME")
            if not tbl or not col:
                continue
            grouped.setdefault(tbl, []).append(
                {
                    "name": col,
                    "ordinal": r.get("ORDINAL_POSITION"),
                    "default_val": r.get("COLUMN_DEFAULT"),
                    "nullable": str(r.get("IS_NULLABLE", "")).upper() == "YES",
                    "col_type": r.get("DATA_TYPE"),
                    "column_type_full": r.get("COLUMN_TYPE"),
                    "key_type": r.get("COLUMN_KEY"),
                    "comment": r.get("COLUMN_COMMENT"),
                }
            )
        return grouped

    def list_columns(self, db_name: str, table_name: str) -> list[dict]:
        rows = self._query(
            """
            SELECT COLUMN_NAME, ORDINAL_POSITION, COLUMN_DEFAULT, IS_NULLABLE,
                   DATA_TYPE, COLUMN_TYPE, COLUMN_KEY, COLUMN_COMMENT
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = :db AND TABLE_NAME = :tbl
            ORDER BY ORDINAL_POSITION
            """,
            {"db": db_name, "tbl": table_name},
        )
        return [
            {
                "name": r.get("COLUMN_NAME"),
                "ordinal": r.get("ORDINAL_POSITION"),
                "default_val": r.get("COLUMN_DEFAULT"),
                "nullable": str(r.get("IS_NULLABLE", "")).upper() == "YES",
                "col_type": r.get("DATA_TYPE"),
                "column_type_full": r.get("COLUMN_TYPE"),
                "key_type": r.get("COLUMN_KEY"),
                "comment": r.get("COLUMN_COMMENT"),
            }
            for r in rows
            if r.get("COLUMN_NAME")
        ]


class RedshiftCollector:
    """给定连接信息，扫 Redshift information_schema。"""

    _SYSTEM_SCHEMAS = {
        "information_schema",
        "pg_catalog",
        "pg_internal",
        "pg_toast",
    }

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str | None = None,
        timeout: int = 30,
    ) -> None:
        self._kwargs = {
            "host": host,
            "port": port,
            "database": database or "dev",
            "user": username,
            "password": password,
            "timeout": timeout,
        }

    def _connect(self):
        import redshift_connector

        redshift_connector.paramstyle = "pyformat"
        return redshift_connector.connect(**self._kwargs)

    def _query(self, sql: str, params: dict | None = None) -> list[dict[str, Any]]:
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                try:
                    cur.execute(sql, params or {})
                    columns = [desc[0] for desc in cur.description or []]
                    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
                finally:
                    cur.close()
            finally:
                conn.close()
        except CustomException:
            raise
        except Exception as e:  # noqa: BLE001
            raise CustomException(msg=f"Redshift 采集查询失败: {e}")

    def test_connection(self) -> bool:
        self._query("SELECT 1")
        return True

    def list_databases(self) -> list[str]:
        rows = self._query("SELECT schema_name FROM information_schema.schemata")
        return [
            r["schema_name"]
            for r in rows
            if r.get("schema_name") and r["schema_name"] not in self._SYSTEM_SCHEMAS
        ]

    def list_tables(self, schema_name: str) -> list[dict]:
        rows = self._query(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = %(schema_name)s
            ORDER BY table_name
            """,
            {"schema_name": schema_name},
        )
        return [
            {
                "table_name": r.get("table_name"),
                "table_type": r.get("table_type"),
                "row_count": None,
                "data_size": None,
                "comment": None,
            }
            for r in rows
            if r.get("table_name")
        ]

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _column_type_full(cls, row: dict[str, Any]) -> str | None:
        data_type = row.get("data_type")
        if not data_type:
            return None
        base = str(data_type).strip()
        lower = base.lower()

        char_len = cls._int_or_none(row.get("character_maximum_length"))
        if lower in {"character varying", "varchar", "character", "char"} and char_len and char_len > 0:
            return f"{base}({char_len})"

        precision = cls._int_or_none(row.get("numeric_precision"))
        scale = cls._int_or_none(row.get("numeric_scale"))
        if lower in {"numeric", "decimal"}:
            if precision is not None and scale is not None:
                return f"{base}({precision},{scale})"
            if precision is not None:
                return f"{base}({precision})"

        return base

    @classmethod
    def _column_payload(cls, row: dict[str, Any], name_key: str) -> dict[str, Any]:
        column_type = cls._column_type_full(row)
        return {
            "name": row.get(name_key),
            "ordinal": row.get("ordinal_position"),
            "default_val": row.get("column_default"),
            "nullable": str(row.get("is_nullable", "")).upper() == "YES",
            "col_type": column_type,
            "column_type_full": column_type,
            "key_type": None,
            "comment": None,
        }

    def list_columns_by_db(self, schema_name: str) -> dict[str, list[dict]]:
        rows = self._query(
            """
            SELECT table_name, column_name, ordinal_position, column_default, is_nullable,
                   data_type, character_maximum_length, numeric_precision, numeric_scale,
                   datetime_precision
            FROM information_schema.columns
            WHERE table_schema = %(schema_name)s
            ORDER BY table_name, ordinal_position
            """,
            {"schema_name": schema_name},
        )
        grouped: dict[str, list[dict]] = {}
        for r in rows:
            tbl = r.get("table_name")
            col = r.get("column_name")
            if not tbl or not col:
                continue
            grouped.setdefault(tbl, []).append(self._column_payload(r, "column_name"))
        return grouped

    def list_columns(self, schema_name: str, table_name: str) -> list[dict]:
        rows = self._query(
            """
            SELECT column_name, ordinal_position, column_default, is_nullable, data_type,
                   character_maximum_length, numeric_precision, numeric_scale, datetime_precision
            FROM information_schema.columns
            WHERE table_schema = %(schema_name)s AND table_name = %(table_name)s
            ORDER BY ordinal_position
            """,
            {"schema_name": schema_name, "table_name": table_name},
        )
        return [
            self._column_payload(r, "column_name")
            for r in rows
            if r.get("column_name")
        ]
