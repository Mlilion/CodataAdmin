-- 裸 SQL(execute_sql)调用审计表
-- 幂等: CREATE TABLE IF NOT EXISTS。新库由 create_all 直接建。
-- ★ 本文件注释与 COMMENT 文本里都不要写分号 —— 按分号切语句的执行器会在字符串中间切断
--   (2026-08-01 踩过, 2026-08-02 的 1301bbb6 又踩过一次: 这行警示自己写了个分号,
--    结果 CREATE TABLE 被切掉一半 —— 写"不要做 X"的警示时那句话自己不能做 X)
--
-- 为什么需要: execute_sql(设计 §24)让 agent 能执行自己写的只读 SQL, 访问面是
--   "只读账号可见的任何表"。当时的结论是"真正的边界在数仓账号授权上" —— 那仍对,
--   但它只管**能读什么**, 不管**读了什么**。没有本表就无法回答「谁在读哪张表」。
--
-- 为什么不塞进 sem_query_log: 那张表结构围绕「自然语言 → 指标 code」建立, 服务于同义词
--   挖掘。塞进去 ①SQL 文本会作为"候选别名"出现在治理页待审列表(alias_candidates 按
--   raw_query 聚合) ②会被 _log_adoption 的采纳回填误伤。
--
-- 为什么记 SQL 原文: 记原文才能回答"他到底查了什么"、出事后能复现。代价是本表自己含
--   原文, 故读端点用**独立权限点** rawsqllog:list, 不与 querylog:list 合并 ——
--   "看同义词候选"与"看别人查了什么"是两种授权。
--
-- 为什么连被拒绝的调用也记: permission_denied 那条路才是安全信号(能看出某 token 是否在
--   反复试探写操作)。只记成功会恰好漏掉最该被看见的那部分。

CREATE TABLE IF NOT EXISTS `sem_raw_sql_log` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  -- varchar(64) 对齐 ModelMixin.uuid = String(64), 否则 create_all 与本补丁建出两种表结构
  `uuid` varchar(64) NOT NULL,
  `caller` varchar(50) DEFAULT NULL COMMENT '调用方(MCP token 标识 / rest)',
  `raw_sql` text NOT NULL COMMENT 'agent 提交的 SQL 原文',
  `executed_sql` text COMMENT '实际执行的 SQL(含自动补的 LIMIT), 被拒时为空',
  `tables` varchar(500) DEFAULT NULL COMMENT '涉及的表名(逗号分隔, sqlglot 解析)',
  `ok` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否成功',
  `error_type` varchar(30) DEFAULT NULL COMMENT 'permission_denied/syntax_error/execution_failed/not_configured',
  `error` text COMMENT '错误原文(截断)',
  `row_count` int DEFAULT NULL COMMENT '返回行数',
  `truncated` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否因 LIMIT 截断',
  `duration_ms` int DEFAULT NULL COMMENT '耗时(毫秒)',
  `logged_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0,
  `created_time` datetime DEFAULT NULL,
  `updated_time` datetime DEFAULT NULL,
  `deleted_time` datetime DEFAULT NULL,
  `tenant_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sem_raw_sql_log_uuid` (`uuid`),
  KEY `ix_raw_sql_log_logged_at` (`logged_at`),
  KEY `ix_raw_sql_log_caller` (`caller`),
  KEY `ix_raw_sql_log_error_type` (`error_type`),
  CONSTRAINT `fk_sem_raw_sql_log_tenant` FOREIGN KEY (`tenant_id`)
    REFERENCES `platform_tenant` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='裸 SQL(execute_sql)调用审计';

-- ↓↓↓ 回滚(手工执行时自己补分号 —— 这里不写, 见文件头的告警) ↓↓↓
-- DROP TABLE IF EXISTS `sem_raw_sql_log`
