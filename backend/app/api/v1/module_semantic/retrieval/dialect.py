"""Semantic SQL dialect selection."""

from app.config.setting import settings


def current_query_dialect() -> str:
    return str(settings.SEM_QUERY_DIALECT).strip().lower()


def sqlglot_dialect() -> str:
    return current_query_dialect()
