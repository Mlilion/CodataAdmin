import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.base_schema import AuthSchema


def test_redshift_collector_filters_system_schemas(monkeypatch):
    from app.api.v1.module_semantic.catalog.collector import RedshiftCollector

    collector = RedshiftCollector(
        host="redshift.example",
        port=5439,
        username="readonly",
        password="secret",
        database="dev",
    )

    monkeypatch.setattr(
        collector,
        "_query",
        lambda sql, params=None: [
            {"schema_name": "public"},
            {"schema_name": "pg_catalog"},
            {"schema_name": "information_schema"},
        ],
    )

    assert collector.list_databases() == ["public"]


def test_redshift_collector_query_uses_driver_and_closes(monkeypatch):
    from app.api.v1.module_semantic.catalog.collector import RedshiftCollector

    class FakeCursor:
        description = [("id",)]

        def __init__(self):
            self.closed = False
            self.executed = None

        def execute(self, sql, params):
            self.executed = (sql, params)

        def fetchall(self):
            return [(1,)]

        def close(self):
            self.closed = True

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.closed = False

        def cursor(self):
            return self.cursor_obj

        def close(self):
            self.closed = True

    state = {}

    def connect(**kwargs):
        conn = FakeConnection()
        state["kwargs"] = kwargs
        state["conn"] = conn
        return conn

    fake_driver = SimpleNamespace(connect=connect, paramstyle=None)
    monkeypatch.setitem(sys.modules, "redshift_connector", fake_driver)

    collector = RedshiftCollector(
        host="redshift.example",
        port=5439,
        username="readonly",
        password="secret",
    )

    rows = collector._query("SELECT * FROM t WHERE s = %(schema_name)s", {"schema_name": "public"})

    assert rows == [{"id": 1}]
    assert fake_driver.paramstyle == "pyformat"
    assert state["kwargs"]["database"] == "dev"
    assert state["kwargs"]["user"] == "readonly"
    assert state["conn"].cursor_obj.executed == (
        "SELECT * FROM t WHERE s = %(schema_name)s",
        {"schema_name": "public"},
    )
    assert state["conn"].cursor_obj.closed is True
    assert state["conn"].closed is True


def test_redshift_collector_maps_table_and_column_shapes(monkeypatch):
    from app.api.v1.module_semantic.catalog.collector import RedshiftCollector

    collector = RedshiftCollector(
        host="redshift.example",
        port=5439,
        username="readonly",
        password="secret",
        database="dev",
    )

    def _query(sql, params=None):
        if "information_schema.tables" in sql:
            assert params == {"schema_name": "public"}
            return [{"table_name": "orders", "table_type": "BASE TABLE"}]
        if "information_schema.columns" in sql and "AND table_name" not in sql:
            assert params == {"schema_name": "public"}
            return [
                {
                    "table_name": "orders",
                    "column_name": "amount",
                    "ordinal_position": 2,
                    "is_nullable": "NO",
                    "data_type": "numeric",
                    "column_default": "0",
                    "character_maximum_length": None,
                    "numeric_precision": 18,
                    "numeric_scale": 2,
                }
            ]
        if "information_schema.columns" in sql and "AND table_name" in sql:
            assert params == {"schema_name": "public", "table_name": "orders"}
            return [
                {
                    "column_name": "order_id",
                    "ordinal_position": 1,
                    "is_nullable": "NO",
                    "data_type": "character varying",
                    "column_default": None,
                    "character_maximum_length": 500,
                    "numeric_precision": None,
                    "numeric_scale": None,
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(collector, "_query", _query)

    assert collector.list_tables("public") == [
        {
            "table_name": "orders",
            "table_type": "BASE TABLE",
            "row_count": None,
            "data_size": None,
            "comment": None,
        }
    ]
    assert collector.list_columns_by_db("public") == {
        "orders": [
            {
                "name": "amount",
                "ordinal": 2,
                "default_val": "0",
                "nullable": False,
                "col_type": "numeric(18,2)",
                "column_type_full": "numeric(18,2)",
                "key_type": None,
                "comment": None,
            }
        ]
    }
    assert collector.list_columns("public", "orders") == [
        {
            "name": "order_id",
            "ordinal": 1,
            "default_val": None,
            "nullable": False,
            "col_type": "character varying(500)",
            "column_type_full": "character varying(500)",
            "key_type": None,
            "comment": None,
        }
    ]


@pytest.mark.asyncio
async def test_build_collector_dispatches_redshift(monkeypatch):
    from app.api.v1.module_semantic.catalog.service import CatalogService

    svc = CatalogService(AuthSchema(db=None, check_data_scope=False))
    ds = SimpleNamespace(
        ds_type="redshift",
        host="redshift.example",
        port=5439,
        username="readonly",
        password_enc="",
        default_database="dev",
    )

    class FakeCollector:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("app.api.v1.module_semantic.catalog.service.RedshiftCollector", FakeCollector)
    collector = svc._build_collector(ds)

    assert isinstance(collector, FakeCollector)
    assert collector.kwargs["host"] == "redshift.example"
    assert collector.kwargs["port"] == 5439
    assert collector.kwargs["database"] == "dev"


def test_datasource_schema_accepts_only_supported_types():
    from app.api.v1.module_semantic.catalog.schema import DatasourceCreateSchema

    assert DatasourceCreateSchema(
        name="rs",
        ds_type="Redshift",
        host="redshift.example",
        port=5439,
        username="readonly",
    ).ds_type == "redshift"

    with pytest.raises(ValidationError):
        DatasourceCreateSchema(
            name="bad",
            ds_type="oracle",
            host="oracle.example",
            port=1521,
            username="readonly",
        )
