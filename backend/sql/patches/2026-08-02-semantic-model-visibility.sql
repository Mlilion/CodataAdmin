-- 语义模型可见性 + 用户授权（MCP 用户隔离）
-- 新库由 create_all 直接建。本补丁给已存在的库加列/建表。
-- 幂等: 用 information_schema 判重。
-- ★ 注释里不要写分号 —— 按分号切语句的执行器会在注释中间切断（2026-08-01 踩到过，
--   本行自己也踩过一次：原文写了个分号，导致下面第一条 SET 从未执行）。
--
-- 为什么需要：MCP 是**服务级鉴权**（token 对应调用方，没有"人"），四个工具都用
-- AuthSchema(user=None, check_data_scope=False)，所以一个 token 能看到平台上
-- **所有已发布**的模型与指标。
--
-- ★★ 关键：平台已有完整的数据权限引擎（core/permission.py），但它**挂在 CRUD 层**，
-- 而 MCP 读路径是 select() 直查（hydrate 取快照 / mcp/server 直查模型表）。所以把
-- check_data_scope 翻成 True **什么都不会发生** —— 问题不是"开关关着"，
-- 而是"这条路不经过那个开关"。故在该路径上单独做判定（retrieval/visibility.py）。
--
-- ★ visibility 默认 public：若一律按白名单，升级瞬间所有模型对所有 token 都不可见
--   （白名单是空的），是可用性倒退。隔离应当是**显式启用**的能力。
--
-- ★ 不复用 data_scope 部门树：那套过滤 created_id(谁建的)，而语义模型通常由数据团队
--   建、给多个业务方看 —— 按 created_id 过滤会变成"数据团队建的只有数据团队能看"。

-- sem_model.visibility
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'sem_model' AND column_name = 'visibility');
SET @sql := IF(@c = 0, 'ALTER TABLE sem_model ADD COLUMN visibility VARCHAR(20) NOT NULL DEFAULT ''public'' COMMENT ''可见性(public 所有调用方 / restricted 仅授权用户)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @i := (SELECT COUNT(1) FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'sem_model' AND index_name = 'ix_sem_model_visibility');
SET @sql := IF(@i = 0, 'ALTER TABLE sem_model ADD INDEX ix_sem_model_visibility (visibility)', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- sem_model_grant（按模型逐个授权；粒度比角色细、也不随部门树变动）
CREATE TABLE IF NOT EXISTS `sem_model_grant` (
  `id` int NOT NULL AUTO_INCREMENT,
  `uuid` varchar(50) NOT NULL,
  `model_id` int NOT NULL COMMENT '模型ID',
  `user_id` int NOT NULL COMMENT '被授权用户ID',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0,
  `created_time` datetime DEFAULT NULL,
  `updated_time` datetime DEFAULT NULL,
  `deleted_time` datetime DEFAULT NULL,
  `tenant_id` int NOT NULL,
  `created_id` int DEFAULT NULL,
  `updated_id` int DEFAULT NULL,
  `deleted_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sem_model_grant_uuid` (`uuid`),
  UNIQUE KEY `uq_model_grant` (`model_id`, `user_id`),
  KEY `ix_sem_model_grant_model` (`model_id`),
  KEY `ix_sem_model_grant_user` (`user_id`),
  CONSTRAINT `fk_sem_model_grant_model` FOREIGN KEY (`model_id`) REFERENCES `sem_model` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_sem_model_grant_user` FOREIGN KEY (`user_id`) REFERENCES `sys_user` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='语义模型用户授权表';

-- ↓↓↓ 启用方式 ↓↓↓
-- ★ 首选走 UI：语义模型页 → 选中模型 → 编辑（可见性改「限制可见」）→「授权管理」加人。
--   需要 module_semantic:model:grant 权限（见 2026-08-02-semantic-model-grant-permission.sql）。
--   本补丁刚落地时管理面还不存在，只能写 SQL；现已有接口与页面，下面的 SQL 仅作应急备用。
--
-- 应急 SQL 示例（把某模型改为限制可见并授权给某人）：
-- UPDATE sem_model SET visibility = 'restricted' WHERE `code` = 'commercial_revenue';
-- INSERT INTO sem_model_grant (uuid, model_id, user_id, is_deleted, created_time, tenant_id)
--   SELECT UUID(), m.id, u.id, 0, NOW(), 1
--     FROM sem_model m JOIN sys_user u ON u.username = 'zhangsan'
--    WHERE m.`code` = 'commercial_revenue';
-- ★ token 还要在 SEM_MCP_TOKENS 里绑上用户名才有"人"的身份：
--   SEM_MCP_TOKENS = "codata:tok1:svc_codata,ops:tok2:zhangsan"

-- ↓↓↓ 回滚 ↓↓↓
-- DROP TABLE IF EXISTS `sem_model_grant`;
-- ALTER TABLE sem_model DROP INDEX ix_sem_model_visibility;
-- ALTER TABLE sem_model DROP COLUMN visibility;
