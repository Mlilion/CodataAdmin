-- P3 R2: 维度别名 + 知识治理列（现有库补丁）
-- 新库由 create_all 直接建含新列。本补丁给已存在的表 ALTER 加列。
-- 幂等: 用 information_schema 判断列是否已存在再加（MySQL 无通用 IF NOT EXISTS ADD COLUMN）。
--
-- 用途:
--   sem_dimension.aliases      —— 别名精确匹配是检索最强信号(0.93 档), 指标已有该列, 维度补齐
--   sem_knowledge 治理四列      —— 新鲜度检测(reviewed_at + review_interval_days)、
--                                 目标失效级联(needs_review)、治理到人(owner_id)

-- sem_dimension.aliases
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_dimension' AND column_name = 'aliases');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_dimension ADD COLUMN aliases TEXT NULL COMMENT ''别名(JSON数组字符串,检索精确匹配用)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_knowledge.owner_id
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_knowledge' AND column_name = 'owner_id');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_knowledge ADD COLUMN owner_id INT NULL COMMENT ''知识负责人ID''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @c := (SELECT COUNT(1) FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'sem_knowledge' AND index_name = 'ix_sem_knowledge_owner_id');
SET @sql := IF(@c = 0, 'CREATE INDEX ix_sem_knowledge_owner_id ON sem_knowledge (owner_id)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_knowledge.reviewed_at
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_knowledge' AND column_name = 'reviewed_at');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_knowledge ADD COLUMN reviewed_at DATETIME NULL COMMENT ''最近复核时间''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_knowledge.review_interval_days
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_knowledge' AND column_name = 'review_interval_days');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_knowledge ADD COLUMN review_interval_days INT NOT NULL DEFAULT 180 COMMENT ''复核周期(天)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_knowledge.needs_review
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_knowledge' AND column_name = 'needs_review');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_knowledge ADD COLUMN needs_review TINYINT(1) NOT NULL DEFAULT 0 COMMENT ''待复核(引用目标失效时级联置位)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @c := (SELECT COUNT(1) FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'sem_knowledge' AND index_name = 'ix_sem_knowledge_needs_review');
SET @sql := IF(@c = 0, 'CREATE INDEX ix_sem_knowledge_needs_review ON sem_knowledge (needs_review)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ↓↓↓ 回滚 ↓↓↓
-- DROP INDEX ix_sem_knowledge_needs_review ON sem_knowledge;
-- DROP INDEX ix_sem_knowledge_owner_id ON sem_knowledge;
-- ALTER TABLE sem_knowledge DROP COLUMN needs_review, DROP COLUMN review_interval_days,
--   DROP COLUMN reviewed_at, DROP COLUMN owner_id;
-- ALTER TABLE sem_dimension DROP COLUMN aliases;
