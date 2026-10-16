-- 语义检索 query 日志 + 采纳标记（参考方案 §6「同义词从历史 query + 采纳日志持续挖掘」）
-- 幂等：CREATE TABLE IF NOT EXISTS。
--
-- 为什么在主库而非检索副库（sem_retrieval_*）：
--   副库定位是"派生副本，可随时 rebuild"，而 query 日志是**原始事实**，丢了无法重建。
--   放副库会被 rebuild 语义误伤。挖掘产出的别名再写回副库 sem_retrieval_alias
--   （该表 source 字段已预留 'mined' 值）。
--
-- adopted 三态是刻意的：NULL=未反馈 / 1=正例 / 0=负例。
--   绝大多数日志会是 NULL（用户不会逐条点赞），把 NULL 当正例会把检索的错误答案
--   固化成别名让错误自我强化，当负例会把大量正确结果误判为失败。挖掘 SQL 只取 =1。

CREATE TABLE IF NOT EXISTS `sem_query_log` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `uuid` varchar(50) NOT NULL,
  `raw_query` text NOT NULL COMMENT '用户原话(未归一化, 挖掘原料)',
  `normalized_query` varchar(500) DEFAULT NULL COMMENT '归一化后(剥时间词/虚词)',
  `model_id` int DEFAULT NULL COMMENT '所查语义模型',
  `matched_type` varchar(20) DEFAULT NULL COMMENT 'top1 节点类型',
  `matched_code` varchar(100) DEFAULT NULL COMMENT 'top1 业务 code',
  `matched_score` double DEFAULT NULL COMMENT 'top1 相关性分数',
  `match_path` varchar(20) DEFAULT NULL COMMENT '命中路径(exact/alias/fts/vector)',
  `candidate_count` int NOT NULL DEFAULT 0 COMMENT '候选数(0 表示零召回)',
  `adopted` tinyint(1) DEFAULT NULL COMMENT '是否采纳 top1。NULL=未反馈(不可当负例)',
  `adopted_code` varchar(100) DEFAULT NULL COMMENT '实际采纳的 code。与 matched_code 不同 = 排序错误样本',
  `asked_by_id` int DEFAULT NULL COMMENT '提问用户 ID',
  `feedback_note` text COMMENT '人工反馈备注',
  `logged_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录时间',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0,
  `created_time` datetime DEFAULT NULL,
  `updated_time` datetime DEFAULT NULL,
  `deleted_time` datetime DEFAULT NULL,
  `tenant_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sem_query_log_uuid` (`uuid`),
  KEY `ix_query_log_logged_at` (`logged_at`),
  KEY `ix_query_log_adopted` (`adopted`),
  KEY `ix_query_log_matched` (`matched_type`, `matched_code`),
  CONSTRAINT `fk_sem_query_log_tenant` FOREIGN KEY (`tenant_id`)
    REFERENCES `platform_tenant` (`id`) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='语义检索 query 日志(同义词挖掘与召回质量分析的输入)';

-- ---------------------------------------------------------------- 菜单 + 权限点
-- 语义中心下新增「同义词治理」菜单页：
--   GET  /semantic/querylog/alias-candidates → module_semantic:querylog:list
--   GET  /semantic/querylog/zero-recall      → module_semantic:querylog:list
--   POST /semantic/querylog/approve-alias    → module_semantic:querylog:approve
--
-- 为什么建独立菜单页（而非挂成语义模型页的按钮）：这是**周期性运营动作**
-- （定期看候选、逐条采纳），有自己的列表与筛选，不是某个模型的附属操作。
--
-- 采纳权限与查看权限**分开**：看候选是分析动作，采纳会改变检索行为（别名写活表并同步副库，
-- 立即生效），二者风险不同级，不该共用一个权限点。
--
-- 幂等：按 route_name='SemanticQueryLog' 判断。前置依赖「语义中心」父节点。

SET @exists_ql := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'SemanticQueryLog');
SET @pid_semantic := (SELECT id FROM platform_menu WHERE route_name = 'Semantic' LIMIT 1);

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,
   `route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,
   `client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'同义词治理',2,9,'module_semantic:querylog:list','ri:chat-search-line',
       'SemanticQueryLog','querylog','module_semantic/querylog/index',0,1,0,'同义词治理',0,
       'pc',0,0,0,'tenant',0,'别名挖掘候选与零召回清单(逐条人工采纳)',@pid_semantic
FROM DUAL WHERE @exists_ql = 0 AND @pid_semantic IS NOT NULL;
SET @pid_ql := LAST_INSERT_ID();

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,
   `hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,
   `scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,v.descr,@pid_ql
FROM (
  SELECT '查询' AS name, 1 AS ord, 'module_semantic:querylog:list' AS perm, '查看别名挖掘候选与零召回清单' AS descr UNION ALL
  SELECT '采纳同义词', 2, 'module_semantic:querylog:approve', '把挖掘出的别名候选并进资产(改变检索行为)'
) v
WHERE @exists_ql = 0 AND @pid_semantic IS NOT NULL;

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (m.route_name = 'SemanticQueryLog' OR m.permission LIKE 'module_semantic:querylog:%')
WHERE r.code IN ('SUPER_ADMIN','ADMIN')
  AND @exists_ql = 0 AND @pid_semantic IS NOT NULL;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name='SemanticQueryLog' OR permission LIKE 'module_semantic:querylog:%') t);
-- DELETE FROM platform_menu WHERE route_name = 'SemanticQueryLog' OR permission LIKE 'module_semantic:querylog:%';
-- DROP TABLE IF EXISTS `sem_query_log`;
