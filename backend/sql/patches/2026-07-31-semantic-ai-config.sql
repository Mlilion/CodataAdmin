-- 嵌入模型配置表 + 权限 + 菜单（系统设置 → AI 配置）
-- 幂等：建表用 IF NOT EXISTS；权限逐条判重。
--
-- 为什么独立建表而非复用系统参数：系统参数是 key-value，存不下一组带加密 api_key 的
-- 结构化配置；module_ai 的模型配置是 Redis 存 + 按用户（聊天模型个人偏好），
-- 而嵌入模型是平台级 —— 检索索引全局共用，每人一份会导致向量维度不一致。

CREATE TABLE IF NOT EXISTS `sem_ai_config` (
  `id` int NOT NULL AUTO_INCREMENT,
  `uuid` varchar(50) NOT NULL,
  `provider` varchar(20) NOT NULL DEFAULT 'openai_like' COMMENT '提供商(openai_like/tei)',
  `base_url` varchar(255) DEFAULT NULL COMMENT '端点 Base URL',
  `api_key_enc` text COMMENT 'API Key(Fernet 加密)',
  `model` varchar(120) DEFAULT NULL COMMENT '模型名',
  `dimensions` int NOT NULL DEFAULT 1024 COMMENT '向量维度(改动需重建 pgvector 列)',
  `enabled` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否启用向量路',
  `description` text COMMENT '备注',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0,
  `created_time` datetime DEFAULT NULL,
  `updated_time` datetime DEFAULT NULL,
  `deleted_time` datetime DEFAULT NULL,
  `tenant_id` int DEFAULT NULL,
  `created_id` int DEFAULT NULL,
  `updated_id` int DEFAULT NULL,
  `deleted_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_sem_ai_config_uuid` (`uuid`),
  KEY `ix_sem_ai_config_is_deleted` (`is_deleted`),
  KEY `ix_sem_ai_config_tenant_id` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='嵌入模型配置(平台级单例)';

-- ---- 菜单：系统管理 → AI 配置 ----
SET @pid_system := (SELECT id FROM platform_menu WHERE route_path = '/system' AND type = 1 LIMIT 1);
SET @exists_menu := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'SystemAiConfig');

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,
   `route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,
   `client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'AI 配置',2,90,'module_system:aiconfig:detail','ri:robot-2-line',
       'SystemAiConfig','aiconfig','module_system/aiconfig/index',0,1,0,'AI 配置',0,
       'pc',0,0,0,'tenant',0,'嵌入模型配置(向量检索用)',@pid_system
FROM DUAL WHERE @exists_menu = 0 AND @pid_system IS NOT NULL;
SET @pid_ai := (SELECT id FROM platform_menu WHERE route_name = 'SystemAiConfig' LIMIT 1);

-- ---- 权限按钮（逐条判重，便于追加时复跑）----
INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,
   `hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,
   `scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,v.descr,@pid_ai
FROM (
  SELECT '查看配置' AS name, 1 AS ord, 'module_system:aiconfig:detail' AS perm, '读嵌入配置与测试连接' AS descr UNION ALL
  SELECT '保存配置', 2, 'module_system:aiconfig:update', '改嵌入配置(维度变更会重建向量索引)'
) v
WHERE @pid_ai IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM (SELECT permission FROM platform_menu) p WHERE p.permission = v.perm);

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (m.route_name = 'SystemAiConfig' OR m.permission LIKE 'module_system:aiconfig:%')
WHERE r.code IN ('SUPER_ADMIN','ADMIN');

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name='SystemAiConfig' OR permission LIKE 'module_system:aiconfig:%') t);
-- DELETE FROM platform_menu WHERE permission LIKE 'module_system:aiconfig:%' OR route_name='SystemAiConfig';
-- DROP TABLE IF EXISTS sem_ai_config;
