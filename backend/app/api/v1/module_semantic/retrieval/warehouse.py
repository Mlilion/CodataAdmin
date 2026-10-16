"""Warehouse connection selection for semantic retrieval."""

from app.config.setting import settings
from app.core.exceptions import CustomException

from .dialect import current_query_dialect


def build_readonly_connection(*, timeout: int):
    dialect = current_query_dialect()
    common = {
        "host": settings.SEM_QUERY_HOST,
        "port": settings.SEM_QUERY_PORT,
        "username": settings.SEM_QUERY_USER,
        "password": settings.SEM_QUERY_PASSWORD,
        "database": settings.SEM_QUERY_DB or None,
        "timeout": timeout,
    }
    if dialect == "redshift":
        from .executor import RedshiftReadOnlyConnection

        return RedshiftReadOnlyConnection(**common)
    if dialect in {"starrocks", "mysql"}:
        from .executor import StarRocksReadOnlyConnection

        return StarRocksReadOnlyConnection(**common)
    raise CustomException(msg=f"不支持的 SEM_QUERY_DIALECT: {settings.SEM_QUERY_DIALECT}")
