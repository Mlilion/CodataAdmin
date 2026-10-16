-- 上游变更待办箱（P1）
-- 新库由 create_all 直接建。本补丁给已存在的库建表 + 修唯一键 + 加菜单。
-- 幂等：CREATE TABLE IF NOT EXISTS + 唯一键按列组成判重 + 菜单按 route_name 判重。
-- ★ 注释里不要写分号 —— 按分号切语句的执行器会在注释中间截断（本目录已踩过两次，
--   最近一次是 1301bbb6：告警自己写了个分号，导致紧跟其后的 SET 从未执行，
--   变量为 NULL 让 INSERT 的 WHERE 不成立 —— 插 0 行且不报错）
--
-- 为什么需要：上游改表后，"哪些语义资产受影响"此前只能靠人工考古
-- （2026-07-31 那次表重命名，善后花了一整轮，6 个连锁问题里 5 个是"没人知道要改这里"）。
--
-- ★ 唯一约束 (db_name, table_name, column_name, change_kind) 是待办箱能用的前提：
--   没有它，每日定时会把同一条"列没了"堆成 30 行。
--   键里必须含 db_name —— dws/dwd/dim/ads 分层同名表是数仓常态，少了它跨库同名表
--   会互相覆盖（Task 4 复审实测：两条只差库名的变更只入库 1 行，第二条被静默吞掉）。

CREATE TABLE IF NOT EXISTS `sem_upstream_change` (
  `id` int NOT NULL AUTO_INCREMENT,
  `uuid` varchar(64) NOT NULL,
  `change_kind` varchar(20) NOT NULL COMMENT '变更类型(column_added/column_removed/column_type_changed/table_removed)',
  `db_name` varchar(100) NOT NULL COMMENT '库名',
  `table_name` varchar(100) NOT NULL COMMENT '表名',
  `column_name` varchar(100) DEFAULT NULL COMMENT '列名(表级变更为空)',
  `table_id` int DEFAULT NULL COMMENT '物理表ID',
  `column_id` int DEFAULT NULL COMMENT '物理列ID',
  `before_value` varchar(200) DEFAULT NULL COMMENT '变更前值',
  `after_value` varchar(200) DEFAULT NULL COMMENT '变更后值',
  `impact_level` varchar(10) NOT NULL COMMENT '影响级别(bound/sql_ref/table_only)',
  `impact_detail` text COMMENT '受影响对象清单(JSON)',
  `status` varchar(10) NOT NULL DEFAULT 'pending' COMMENT '状态(pending/resolved/ignored)',
  `resolve_note` text COMMENT '处理说明(ignored 时必填)',
  `resolved_at` datetime DEFAULT NULL COMMENT '处理时间',
  `resolved_id` int DEFAULT NULL COMMENT '处理人',
  `detected_at` datetime NOT NULL COMMENT '首次检出时间',
  `last_seen_at` datetime DEFAULT NULL COMMENT '最近一次同步仍看到',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0,
  `created_time` datetime DEFAULT NULL,
  `updated_time` datetime DEFAULT NULL,
  `deleted_time` datetime DEFAULT NULL,
  `tenant_id` int NOT NULL,
  `created_id` int DEFAULT NULL,
  `updated_id` int DEFAULT NULL,
  `deleted_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sem_upstream_change_uuid` (`uuid`),
  UNIQUE KEY `uq_upstream_change` (`db_name`, `table_name`, `column_name`, `change_kind`),
  KEY `ix_upstream_change_status` (`status`),
  KEY `ix_upstream_change_table` (`table_name`),
  CONSTRAINT `fk_upstream_change_table` FOREIGN KEY (`table_id`) REFERENCES `cat_table` (`id`) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_upstream_change_column` FOREIGN KEY (`column_id`) REFERENCES `cat_column` (`id`) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_upstream_change_user` FOREIGN KEY (`resolved_id`) REFERENCES `sys_user` (`id`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='上游物理变更待办';

-- ---------------------------------------------------------------- 修唯一键（存量库）
-- ★★ 为什么必须有这一段：`CREATE TABLE IF NOT EXISTS` 对**已存在**的表是彻底的空操作。
--   本表在加 db_name 进唯一键**之前**就已被 create_all 建到了 dev 库（实测：
--   dev 的 uq_upstream_change 只有 3 列）。只发上面的 CREATE，这类库会永远留着
--   3 列键 —— 而这正是"跨库同名表变更互相覆盖"那个已被实测过的缺陷。
--
-- 判据用 information_schema.statistics 查"这个键里有没有 db_name 这一列"，
-- 而不是硬删重建：删重建不幂等，且在正确的库上白白锁一次表。
-- 加宽唯一键不会因数据冲突而失败：3 列唯一成立 ⇒ 4 列唯一必然成立。
--
-- 用 PREPARE/EXECUTE 而非直接 ALTER，是因为 MySQL 的 ALTER 不支持 IF EXISTS 判条件，
-- 而 DDL 又不能写在 IF 里。@ddl 为 'DO 0' 时等于一条空动作。

SET @idx_exists := (
  SELECT COUNT(1) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'sem_upstream_change'
    AND index_name = 'uq_upstream_change'
);
SET @idx_has_db := (
  SELECT COUNT(1) FROM information_schema.statistics
  WHERE table_schema = DATABASE() AND table_name = 'sem_upstream_change'
    AND index_name = 'uq_upstream_change' AND column_name = 'db_name'
);

SET @ddl := IF(@idx_exists > 0 AND @idx_has_db = 0,
  'ALTER TABLE `sem_upstream_change` DROP INDEX `uq_upstream_change`', 'DO 0');
PREPARE fix_drop FROM @ddl;
EXECUTE fix_drop;
DEALLOCATE PREPARE fix_drop;

SET @ddl := IF(@idx_exists = 0 OR @idx_has_db = 0,
  'ALTER TABLE `sem_upstream_change` ADD UNIQUE KEY `uq_upstream_change` (`db_name`, `table_name`, `column_name`, `change_kind`)',
  'DO 0');
PREPARE fix_add FROM @ddl;
EXECUTE fix_add;
DEALLOCATE PREPARE fix_add;

-- ---------------------------------------------------------------- 菜单
-- 挂在「语义中心」下（处理上游变更是物理目录治理的一部分）。
-- 权限点复用 module_semantic:catalog:list —— 不新增：处理上游变更就是物理目录治理，
-- 它不是一道独立的访问控制闸（与"发 token""授权模型"不同）。

SET @pid_semantic := (SELECT id FROM platform_menu WHERE route_name = 'Semantic' LIMIT 1);
SET @exists_page := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'SemanticUpstreamChange');

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,
   `route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,
   `client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'上游变更',2,13,'module_semantic:catalog:list','ri:git-pull-request-line',
       'SemanticUpstreamChange','upstream-change','module_semantic/upstreamchange/index',0,1,0,'上游变更',0,
       'pc',0,0,0,'tenant',0,'上游改表后受影响的语义资产待办',@pid_semantic
FROM DUAL WHERE @exists_page = 0 AND @pid_semantic IS NOT NULL;

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON m.route_name = 'SemanticUpstreamChange'
WHERE r.code IN ('SUPER_ADMIN','ADMIN');

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name='SemanticUpstreamChange') t);
-- DELETE FROM platform_menu WHERE route_name='SemanticUpstreamChange';
-- DROP TABLE IF EXISTS `sem_upstream_change`;
