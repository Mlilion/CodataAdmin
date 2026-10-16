"""审计表的结构约束。

## 为什么给一张日志表写模型测试

这张表有三个"建错了不报错、只是悄悄失效"的地方，全都在本分支或 sem_query_log 上
踩过：

1. **索引建在 `logged_at` 而非 `created_time`** —— 建错的后果不是报错，而是高频写入
   表上的时间过滤退化成全表扫，量涨起来后治理页越来越慢且没有任何提示原因。
2. **不挂 UserMixin** —— 挂了会给每条日志的读取多带三次 JOIN（created_by/updated_by
   的 loader 展开）。
3. **`ok` 与 `error_type` 必须能表达"被守卫拒绝"** —— 只记成功调用会恰好漏掉最该被
   看见的那部分（某 token 在反复试探写操作）。
"""

from app.api.v1.module_semantic.rawsqllog.model import SemRawSqlLogModel


def test_table_name_is_separate_from_query_log():
    """★ 独立表。塞进 sem_query_log 会污染同义词挖掘语料（设计 §二）。"""
    assert SemRawSqlLogModel.__tablename__ == "sem_raw_sql_log"


def test_does_not_mix_in_user_columns():
    """★ 不挂 UserMixin：高频写入日志，不需要 created_by/updated_by 的 loader 展开。"""
    cols = set(SemRawSqlLogModel.__table__.columns.keys())

    assert "created_id" not in cols
    assert "updated_id" not in cols
    # 但租户列要有（与 sem_query_log 一致）
    assert "tenant_id" in cols


def test_has_all_audit_columns():
    """审计要回答的问题各有一列承载。"""
    cols = set(SemRawSqlLogModel.__table__.columns.keys())

    for name in (
        "caller",        # 谁
        "raw_sql",       # 提交了什么
        "executed_sql",  # 实际执行了什么(含自动补的 LIMIT)
        "tables",        # ★ 读了哪张表
        "ok", "error_type", "error",   # 成败与归因
        "row_count", "truncated", "duration_ms",  # 用量与性能
        "logged_at",     # 何时
    ):
        assert name in cols, f"审计缺列: {name}"


def test_logged_at_is_not_nullable_and_server_defaulted():
    """★★ logged_at 必须 NOT NULL + 有 server default。

    它同时是时间索引的目标列和清理任务的过滤列（`logged_at < cutoff`）。一旦丢了
    NOT NULL/server default，写入方漏传时间就会落下 NULL —— 那些行既不会报错，也永远
    匹配不上 `< cutoff`，于是**永不过期**：审计表带着最敏感的 SQL 原文无限增长。
    """
    col = SemRawSqlLogModel.__table__.columns["logged_at"]

    assert col.nullable is False, "logged_at 可为空 —— NULL 时间戳的行清理任务永远删不掉"
    assert col.server_default is not None, "logged_at 缺 server default —— 漏传即落 NULL"


def test_tables_column_holds_table_names():
    """★ `tables` 是回答「谁读了哪张表」的那一列，类型必须能装下表名列表。

    只断言"列存在"挡不住它被写成 Integer 或 String(10)：前者根本装不下表名，
    后者会把多表查询的表名列表悄悄截断，审计答案随之变错而不报错。
    """
    col = SemRawSqlLogModel.__table__.columns["tables"]

    assert col.type.__class__.__name__ == "String"
    assert col.type.length == 500, f"tables 长度被改成 {col.type.length}，多表查询会被截断"
    assert col.nullable is True  # 解析失败/无表(如 SELECT 1)时留空，不阻断写入


def test_time_index_is_on_logged_at():
    """★★ 索引必须在 logged_at 上。

    建在 created_time 上不会报错，只会让时间窗过滤退化成全表扫 —— 这张表是高频写入，
    量涨起来后治理页越来越慢，且没有任何报错提示原因。sem_query_log 的注释里记着
    这个坑，这里钉住它。
    """
    indexed = {
        tuple(c.name for c in idx.columns)
        for idx in SemRawSqlLogModel.__table__.indexes
    }
    flat = {c for cols in indexed for c in cols}

    assert "logged_at" in flat, f"logged_at 未建索引，实际索引: {indexed}"
    # 这里**不**断言 "created_time" not in flat：ModelMixin 给全仓每张表的 created_time
    # 都带 index=True（sem_query_log 也一样），所以它出现在索引集里是基类的常态，
    # 不构成"建错列"的证据。真正的错法是 logged_at 上**没有**索引 —— 上面那条断言管这个。


def test_caller_and_error_type_are_indexed():
    """按调用方与按错误类型筛选都要走索引（治理页两个主要筛选维度）。"""
    flat = {
        c.name
        for idx in SemRawSqlLogModel.__table__.indexes
        for c in idx.columns
    } | {
        c.name for c in SemRawSqlLogModel.__table__.columns if c.index
    }

    assert "caller" in flat
    assert "error_type" in flat


def test_raw_sql_is_not_length_limited():
    """raw_sql 用 Text：SQL 可以很长，varchar 截断会让审计记录不可复现。"""
    col = SemRawSqlLogModel.__table__.columns["raw_sql"]

    assert col.type.__class__.__name__ == "Text"
    assert col.nullable is False


def test_ok_defaults_are_explicit():
    """ok / truncated 不可为空 —— NULL 的"是否成功"在审计里无法解读。"""
    assert SemRawSqlLogModel.__table__.columns["ok"].nullable is False
    assert SemRawSqlLogModel.__table__.columns["truncated"].nullable is False
