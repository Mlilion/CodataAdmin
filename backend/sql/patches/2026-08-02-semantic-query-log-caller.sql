-- query 日志记录调用方（MCP 多 token 标识）
-- 新库由 create_all 直接建含新列。本补丁给已存在的表 ALTER 加列。
-- 幂等: 用 information_schema 判断列是否已存在再加。
-- ★ 注释里不要写分号 —— 按分号切语句的执行器会在字符串中间切断（2026-08-01 踩到过）。
--   连这句提醒本身也不能写那个字符 —— 否则它自己就是那个 bug（本文件曾如此，
--   导致紧随其后的 SET @c 被吞掉、@c 为 NULL、caller 列根本加不上）。
--
-- 为什么需要：MCP 此前是**单一共享 token**，一个字符串对应"所有调用者" ——
-- 谁调的、该不该调都无法区分，且一处泄露就得全量换、所有接入方一起断。
-- 改为多 token(SEM_MCP_TOKENS = "codata:tok1,ops:tok2")后，日志能记下调用方名。
--
-- ★ 为什么不复用 asked_by_id：那是 int（"提问用户 ID"），而 MCP 调用方是**服务**
--   (Codata agent)、不是 sys_user 里的某一行。硬塞假用户 ID 会污染字段语义，
--   且将来真接入用户身份时无法区分"服务在问"与"某人在问"。两字段并存、各自清晰。
--
-- 治理价值：自动巡检与真人提问的**采纳标准不同** —— 前者的高频问法可能只是脚本里
-- 写死的措辞，采纳成别名没有价值。挖掘接口据此加了 caller 过滤参数。

SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_query_log' AND column_name = 'caller');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_query_log ADD COLUMN caller VARCHAR(50) NULL COMMENT ''调用方(MCP token 标识 / rest)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 挖掘/零召回按调用方过滤要走索引（该表是高频写入，全表扫会越来越慢）
SET @i := (SELECT COUNT(1) FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'sem_query_log' AND index_name = 'ix_query_log_caller');
SET @sql := IF(@i = 0, 'ALTER TABLE sem_query_log ADD INDEX ix_query_log_caller (caller)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ↓↓↓ 回滚 ↓↓↓
-- ALTER TABLE sem_query_log DROP INDEX ix_query_log_caller;
-- ALTER TABLE sem_query_log DROP COLUMN caller;
