-- 阶段4 L4/L5 打通: 给 sem_indicator / sem_dimension 加列（现有库补丁）
-- 新库由 create_all 直接建含新列。本补丁给已存在的表 ALTER 加列。
-- 幂等: 用 information_schema 判断列是否已存在再加（MySQL 无 IF NOT EXISTS ADD COLUMN 通用支持）。

-- sem_indicator.primary_entity_id
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_indicator' AND column_name = 'primary_entity_id');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_indicator ADD COLUMN primary_entity_id INT NULL COMMENT ''主实体ID(L2)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_indicator.available_dimension_ids
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_indicator' AND column_name = 'available_dimension_ids');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_indicator ADD COLUMN available_dimension_ids TEXT NULL COMMENT ''可用维度ID列表(JSON)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_dimension.bind_entity_id
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_dimension' AND column_name = 'bind_entity_id');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_dimension ADD COLUMN bind_entity_id INT NULL COMMENT ''绑定实体ID(L2)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_dimension.bind_field_code
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_dimension' AND column_name = 'bind_field_code');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_dimension ADD COLUMN bind_field_code VARCHAR(50) NULL COMMENT ''绑定字段编码''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ↓↓↓ 回滚 ↓↓↓
-- ALTER TABLE sem_indicator DROP COLUMN primary_entity_id, DROP COLUMN available_dimension_ids;
-- ALTER TABLE sem_dimension DROP COLUMN bind_entity_id, DROP COLUMN bind_field_code;
