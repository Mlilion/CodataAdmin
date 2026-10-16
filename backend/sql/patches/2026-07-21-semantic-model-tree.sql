-- P1 语义模型树: sem_model 三表 + 实体/字段物理绑定FK列 + 「语义模型」树页菜单（现有库补丁）
-- 新库由 create_all + platform_menu.json 种子自带。幂等: CREATE IF NOT EXISTS / information_schema 判列 / route_name 判菜单。
-- 菜单调整: 原「语义模型」(JOIN关系, route_name=SemanticJoin) 改名「JOIN 关系」并让位 order=4。
--          新树页占用「语义模型」名+order=3。
-- ★ 注释里不要写分号: 按分号切语句的执行器会把分号后的正文当成下一条语句的开头
--   (它已不以 -- 开头, 剥不掉)。本文件此前正是如此 —— 上面那行的分号把紧随其后的
--   CREATE TABLE sem_model 污染掉, 那张表在已有库上从未被建出来。

-- ============================================================
-- 1) sem_model（语义模型, 模型树根）
-- ============================================================
CREATE TABLE IF NOT EXISTS `sem_model` (
  `code` varchar(50) NOT NULL COMMENT '模型编码(域内唯一)',
  `name` varchar(100) NOT NULL COMMENT '模型名称',
  `domain_id` int DEFAULT NULL COMMENT '归属业务域ID',
  `description` text COMMENT '分析场景说明(进agent上下文开头)',
  `default_time_grain` varchar(20) DEFAULT NULL COMMENT '默认时间粒度(day/week/month)',
  `version` int NOT NULL DEFAULT 0 COMMENT '发布版本号(每次发布+1)',
  `status` int NOT NULL DEFAULT 0 COMMENT '状态(0:草稿 1:待审 2:已发布 3:已归档)',
  `owner_id` int DEFAULT NULL COMMENT '模型负责人ID',
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(64) NOT NULL COMMENT 'UUID全局唯一标识',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否已删除(0:未删除 1:已删除)',
  `created_time` datetime NOT NULL COMMENT '创建时间',
  `updated_time` datetime NOT NULL COMMENT '更新时间',
  `deleted_time` datetime DEFAULT NULL COMMENT '删除时间',
  `tenant_id` int NOT NULL DEFAULT 1 COMMENT '租户ID',
  `created_id` int DEFAULT NULL COMMENT '创建人ID',
  `updated_id` int DEFAULT NULL COMMENT '更新人ID',
  `deleted_id` int DEFAULT NULL COMMENT '删除人ID',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_sem_model_uuid` (`uuid`),
  KEY `ix_sem_model_code` (`code`),
  KEY `ix_sem_model_domain_id` (`domain_id`),
  KEY `ix_sem_model_status` (`status`),
  KEY `ix_sem_model_tenant_id` (`tenant_id`),
  KEY `ix_sem_model_is_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='语义模型表(模型树根)';

-- ============================================================
-- 2) sem_model_entity（模型↔实体成员, M2M）
-- 注: uk 含软删行, 移除后再添加由应用层"复活"处理
-- ============================================================
CREATE TABLE IF NOT EXISTS `sem_model_entity` (
  `model_id` int NOT NULL COMMENT '模型ID',
  `entity_id` int NOT NULL COMMENT '实体ID',
  `role` varchar(20) NOT NULL DEFAULT 'related' COMMENT '角色(primary主事实/related关联)',
  `sort` int NOT NULL DEFAULT 0 COMMENT '展示顺序',
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(64) NOT NULL COMMENT 'UUID全局唯一标识',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否已删除',
  `created_time` datetime NOT NULL COMMENT '创建时间',
  `updated_time` datetime NOT NULL COMMENT '更新时间',
  `deleted_time` datetime DEFAULT NULL COMMENT '删除时间',
  `tenant_id` int NOT NULL DEFAULT 1 COMMENT '租户ID',
  `created_id` int DEFAULT NULL COMMENT '创建人ID',
  `updated_id` int DEFAULT NULL COMMENT '更新人ID',
  `deleted_id` int DEFAULT NULL COMMENT '删除人ID',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_model_entity` (`model_id`,`entity_id`),
  UNIQUE KEY `ix_sem_model_entity_uuid` (`uuid`),
  KEY `ix_sem_model_entity_model_id` (`model_id`),
  KEY `ix_sem_model_entity_entity_id` (`entity_id`),
  KEY `ix_sem_model_entity_tenant_id` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='语义模型实体成员表';

-- ============================================================
-- 3) sem_model_release（发布快照, P2 编译写入）
-- ============================================================
CREATE TABLE IF NOT EXISTS `sem_model_release` (
  `model_id` int NOT NULL COMMENT '模型ID',
  `version` int NOT NULL COMMENT '发布版本号',
  `snapshot` mediumtext NOT NULL COMMENT '编译后的完整语义上下文(JSON)',
  `node_stats` varchar(500) DEFAULT NULL COMMENT '节点统计(JSON)',
  `checksum` varchar(64) DEFAULT NULL COMMENT 'snapshot 内容hash',
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` varchar(64) NOT NULL COMMENT 'UUID全局唯一标识',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否已删除',
  `created_time` datetime NOT NULL COMMENT '创建时间',
  `updated_time` datetime NOT NULL COMMENT '更新时间',
  `deleted_time` datetime DEFAULT NULL COMMENT '删除时间',
  `tenant_id` int NOT NULL DEFAULT 1 COMMENT '租户ID',
  `created_id` int DEFAULT NULL COMMENT '创建人ID',
  `updated_id` int DEFAULT NULL COMMENT '更新人ID',
  `deleted_id` int DEFAULT NULL COMMENT '删除人ID',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_model_release_version` (`model_id`,`version`),
  UNIQUE KEY `ix_sem_model_release_uuid` (`uuid`),
  KEY `ix_sem_model_release_model_id` (`model_id`),
  KEY `ix_sem_model_release_tenant_id` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='语义模型发布快照表';

-- ============================================================
-- 4) sem_entity.physical_table_id（FK 挂钩 L1 cat_table；只加列+索引, 不加FK约束防历史数据脏）
-- ============================================================
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_entity' AND column_name = 'physical_table_id');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_entity ADD COLUMN physical_table_id INT NULL COMMENT ''挂钩物理表ID(L1)'', ADD INDEX ix_sem_entity_physical_table_id (physical_table_id)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ============================================================
-- 5) sem_field.physical_column_id（FK 挂钩 L1 cat_column）
-- ============================================================
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_field' AND column_name = 'physical_column_id');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_field ADD COLUMN physical_column_id INT NULL COMMENT ''挂钩物理列ID(L1)'', ADD INDEX ix_sem_field_physical_column_id (physical_column_id)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ============================================================
-- 6) best-effort 回填: 文本 physical_* → FK（按 库名+表名 / 列名 匹配, 多数据源重名取最小id）
-- ============================================================
UPDATE sem_entity e
JOIN (
  SELECT t.db_name, t.name, MIN(t.id) AS tbl_id
  FROM cat_table t WHERE t.is_deleted = 0
  GROUP BY t.db_name, t.name
) m ON m.db_name = e.physical_database AND m.name = e.physical_table
SET e.physical_table_id = m.tbl_id
WHERE e.is_deleted = 0 AND e.physical_table_id IS NULL
  AND e.physical_database IS NOT NULL AND e.physical_table IS NOT NULL;

UPDATE sem_field f
JOIN sem_entity e ON e.id = f.entity_id AND e.is_deleted = 0 AND e.physical_table_id IS NOT NULL
JOIN cat_column c ON c.table_id = e.physical_table_id AND c.name = f.physical_column AND c.is_deleted = 0
SET f.physical_column_id = c.id
WHERE f.is_deleted = 0 AND f.physical_column_id IS NULL AND f.physical_column IS NOT NULL;

-- ============================================================
-- 7) 菜单: join 页改名让位 + 新增「语义模型」树页 + 权限点 + 角色授权
-- ============================================================
UPDATE platform_menu SET name='JOIN 关系', title='JOIN 关系', `order`=4 WHERE route_name='SemanticJoin';
UPDATE platform_menu SET `order`=5 WHERE route_name='SemanticIndicator';
UPDATE platform_menu SET `order`=6 WHERE route_name='SemanticDimension';
UPDATE platform_menu SET `order`=7 WHERE route_name='SemanticKnowledge';

SET @exists_model := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'SemanticModel');
SET @pid_semantic := (SELECT id FROM platform_menu WHERE route_name = 'Semantic' LIMIT 1);

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,`route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'语义模型',2,3,'module_semantic:model:list','ri:organization-chart','SemanticModel','model','module_semantic/model/index',0,1,0,'语义模型',0,'pc',0,0,0,'tenant',0,'语义模型树(模型组装/发布)',@pid_semantic
FROM DUAL WHERE @exists_model = 0 AND @pid_semantic IS NOT NULL;
SET @pid_model := LAST_INSERT_ID();

INSERT INTO platform_menu (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,`hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,@pid_model FROM (
  SELECT '查询' AS name, 1 AS ord, 'module_semantic:model:list' AS perm UNION ALL
  SELECT '详情', 2, 'module_semantic:model:detail' UNION ALL
  SELECT '新增', 3, 'module_semantic:model:create' UNION ALL
  SELECT '编辑', 4, 'module_semantic:model:update' UNION ALL
  SELECT '删除', 5, 'module_semantic:model:delete'
) v WHERE @exists_model = 0 AND @pid_semantic IS NOT NULL;

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (m.route_name = 'SemanticModel' OR m.permission LIKE 'module_semantic:model:%')
WHERE r.code IN ('SUPER_ADMIN','ADMIN')
  AND @exists_model = 0 AND @pid_semantic IS NOT NULL;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name='SemanticModel' OR permission LIKE 'module_semantic:model:%') t);
-- DELETE FROM platform_menu WHERE permission LIKE 'module_semantic:model:%' OR route_name='SemanticModel';
-- UPDATE platform_menu SET name='语义模型', title='语义模型', `order`=3 WHERE route_name='SemanticJoin';
-- UPDATE platform_menu SET `order`=4 WHERE route_name='SemanticIndicator';
-- UPDATE platform_menu SET `order`=5 WHERE route_name='SemanticDimension';
-- UPDATE platform_menu SET `order`=6 WHERE route_name='SemanticKnowledge';
-- ALTER TABLE sem_field DROP COLUMN physical_column_id;
-- ALTER TABLE sem_entity DROP COLUMN physical_table_id;
-- DROP TABLE sem_model_release;
-- DROP TABLE sem_model_entity;
-- DROP TABLE sem_model;
