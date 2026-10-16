-- MCP 访问令牌管理（把 token 从 env 搬进库：可轮换、可吊销、可审计）
-- 新库由 create_all 直接建。本补丁给已存在的库建表 + 加权限菜单。
-- 幂等：CREATE TABLE IF NOT EXISTS + 按 permission 判重。
-- ★ 注释里不要写分号 —— 按分号切语句的执行器会在注释中间切断（2026-08-01 踩到过，
--   本行自己也踩过一次：原文写了个分号，导致下面第一条 SET 从未执行）。
--
-- 为什么需要：此前配 token 的**唯一**方式是改 SEM_MCP_TOKENS + 重启服务。
--   · 加一个接入方要改配置文件并重启（等于一次发布）
--   · token 明文躺在 env 里，能读部署配置的人就能拿到全部 token
--   · 无法轮换（换了所有接入方一起断），无法单独吊销（只能整条配置项重写）
--   · 查不到"这个 token 上次何时被用过" → 没人敢删任何 token，僵尸 token 越攒越多
--
-- ★★ token 存 SHA-256 哈希，不存可逆密文。明文只在签发/轮换时返回一次。
--   不复用 crypto_util(Fernet)：那是为"必须还原明文去连外部系统"设计的（数据源密码）。
--   token 校验只需判断"你给的这串对不对" —— 存可逆密文纯属多余风险：
--   SECRET_KEY 在 env 里，库备份 + env 泄露 = 全部 token 明文。存哈希则库泄露也没用。
--   代价是"忘了只能轮换、不能查看"，与 GitHub / Stripe 的 API key 同一语义。
--
-- ★ env 的 SEM_MCP_TOKEN / SEM_MCP_TOKENS **继续有效**（只读兜底，不可在页面吊销）。
--   现网正靠它提供鉴权，升级瞬间让它失效就是让所有接入方一起断 —— 安全倒退。

CREATE TABLE IF NOT EXISTS `sem_mcp_token` (
  `id` int NOT NULL AUTO_INCREMENT,
  `uuid` varchar(50) NOT NULL,
  `name` varchar(50) NOT NULL COMMENT '调用方名(进 query 日志的 caller 字段)',
  `token_hash` varchar(64) NOT NULL COMMENT 'token 的 SHA-256(不可逆, 明文仅签发时返回一次)',
  `token_prefix` varchar(16) NOT NULL DEFAULT '' COMMENT '明文前若干位(仅用于在列表里认出是哪个 token)',
  `bound_username` varchar(50) DEFAULT NULL COMMENT '绑定用户名(模型级隔离的身份, 留空则只能看 public 模型)',
  `last_used_at` datetime DEFAULT NULL COMMENT '最后一次使用时间(没有它没人敢删任何 token)',
  `expires_at` datetime DEFAULT NULL COMMENT '过期时间(留空为长期有效)',
  `revoked` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否已吊销(吊销保留行以留审计痕迹)',
  `description` text COMMENT '用途备注',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0,
  `created_time` datetime DEFAULT NULL,
  `updated_time` datetime DEFAULT NULL,
  `deleted_time` datetime DEFAULT NULL,
  `tenant_id` int NOT NULL,
  `created_id` int DEFAULT NULL,
  `updated_id` int DEFAULT NULL,
  `deleted_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sem_mcp_token_uuid` (`uuid`),
  KEY `ix_sem_mcp_token_hash` (`token_hash`),
  KEY `ix_sem_mcp_token_name` (`name`),
  KEY `ix_sem_mcp_token_revoked` (`revoked`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='MCP 访问令牌';

-- 菜单 + 权限点。挂在「语义中心」下作独立页面（令牌是运维/管理动作，不属于任何建模页）
SET @pid_semantic := (SELECT id FROM platform_menu WHERE route_name = 'Semantic' LIMIT 1);
SET @exists_page := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'SemanticMcpToken');

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,
   `route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,
   `client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'MCP 令牌',2,12,'module_semantic:mcp:token','ri:key-2-line',
       'SemanticMcpToken','mcp-token','module_semantic/mcptoken/index',0,1,0,'MCP 令牌',0,
       'pc',0,0,0,'tenant',0,'MCP 访问令牌的签发/轮换/吊销',@pid_semantic
FROM DUAL WHERE @exists_page = 0 AND @pid_semantic IS NOT NULL;

-- ★★ 权限点单开 module_semantic:mcp:token, 不沿用任何既有权限:
-- **发一个 token 等于发一份访问权** —— 拿到 token 就能问语义口径、能跑参数化数仓查询。
-- 若沿用 retrieval:detail 之类的读权限, "能看索引状态的人"就顺带能给自己发钥匙。
-- 与 model:grant 同一个道理(授权的门不能由被它保护的人自己开)。
SET @pid_token := (SELECT id FROM platform_menu WHERE route_name = 'SemanticMcpToken' LIMIT 1);

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,
   `hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,
   `scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'令牌管理',3,1,'module_semantic:mcp:token','令牌管理',0,1,0,0,'pc',0,0,0,
       'tenant',0,'签发/轮换/吊销 MCP 访问令牌',@pid_token
FROM DUAL
WHERE @pid_token IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM (SELECT id FROM platform_menu WHERE permission = 'module_semantic:mcp:token' AND type = 3) p
  );

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (m.route_name = 'SemanticMcpToken' OR m.permission = 'module_semantic:mcp:token')
WHERE r.code IN ('SUPER_ADMIN','ADMIN');

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name='SemanticMcpToken' OR permission='module_semantic:mcp:token') t);
-- DELETE FROM platform_menu WHERE route_name='SemanticMcpToken' OR permission='module_semantic:mcp:token';
-- DROP TABLE IF EXISTS `sem_mcp_token`;
