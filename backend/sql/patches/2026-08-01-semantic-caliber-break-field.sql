-- 口径分水岭改为结构化字段（现有库补丁）
-- 新库由 create_all 直接建含新列。本补丁给已存在的表 ALTER 加列。
-- 幂等: 用 information_schema 判断列是否已存在再加（MySQL 无通用 IF NOT EXISTS ADD COLUMN）。
--
-- 为什么需要：校验器规则 6(caliber_break)是「数据能查出来、SQL 也没错，但口径错了」
-- 这类最危险错误的唯一防线（EVAL Q10：直接汇总 2025 全年订阅收入会虚高 2-3 倍）。
-- 它此前从知识**正文里正则抠日期**，隐含三个无校验的契约：必须是 caveat 类、
-- 正文必须含"分水岭"或"口径断层"、取正文第一个 YYYY-MM-DD。任一被破坏则护栏
-- **静默失效** —— 不报错不告警，而失效的表现恰恰是"给出一个看起来完全合理的错数字"。
--
-- 真实风险（不是假设）：bill 那条 always 知识的正文现在同时含 2026-06-29(真分水岭)
-- 与 2026-07-31(上游重构说明)，正则只取第一个匹配，日期顺序一变就拦错区间。
--
-- ★★ caliber_break_date 填**最后一个旧口径日**，新口径从次日起生效。
--    bill 的例子：revenue_usd 于 2026-06-29 上线、语义分界是 6/30，故填 2026-06-29。
--    填成 2026-06-30 会让整个分界后移一天。
--
-- 回填是**运营动作**，本补丁只加列。未回填时代码仍走正文正则回退（并打 info 日志
-- 提示回填），所以回填前行为不变 —— 不会因为加了列而让现有护栏失效。

-- sem_knowledge.caliber_break_date
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_knowledge' AND column_name = 'caliber_break_date');
-- ★ 注释里**不要写分号**：按分号切语句的执行器（含本仓用 SQLAlchemy 逐条跑 patch 的
--   脚本）会在字符串中间切断，报一个看起来毫不相关的语法错误。实测踩到过。
--   连这句提醒本身也不能写那个字符 —— 否则它自己就是那个 bug（本文件曾如此，
--   导致紧随其后的 SET @sql 被吞掉、@sql 为 NULL、PREPARE 一并失败）。
SET @sql := IF(@c = 0, 'ALTER TABLE sem_knowledge ADD COLUMN caliber_break_date DATE NULL COMMENT ''口径分水岭(最后一个旧口径日, 次日起为新口径)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_knowledge.caliber_break_note
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_knowledge' AND column_name = 'caliber_break_note');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_knowledge ADD COLUMN caliber_break_note VARCHAR(200) NULL COMMENT ''口径差异一句话说明(进拒答消息给 agent)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ↓↓↓ 回填示例（dev 的 bill 模型；生产请核对 key 与日期后再执行）↓↓↓
-- UPDATE sem_knowledge
--    SET caliber_break_date = '2026-06-29',
--        caliber_break_note = '历史为名义价，直接汇总会显著虚高'
--  WHERE `key` = 'bill-history-amount-caliber-break';
-- ★ 回填后需**重新发布模型**才会进快照（字段是快照数据，非检索层数据）。

-- ↓↓↓ 回滚 ↓↓↓
-- ALTER TABLE sem_knowledge DROP COLUMN caliber_break_date;
-- ALTER TABLE sem_knowledge DROP COLUMN caliber_break_note;
