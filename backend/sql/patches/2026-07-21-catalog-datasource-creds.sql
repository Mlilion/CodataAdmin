-- 自采集: cat_datasource 加连接凭证列 + 物理目录"数据源管理"权限点（现有库补丁）
-- 新库由 create_all/种子自带。幂等: information_schema 判列存在 / route_name 判菜单存在。

-- 1) cat_datasource.username
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'cat_datasource' AND column_name = 'username');
SET @sql := IF(@c = 0, 'ALTER TABLE cat_datasource ADD COLUMN username VARCHAR(100) NULL COMMENT ''连接账号(自采集用)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 2) cat_datasource.password_enc
SET @c := (SELECT COUNT(1) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'cat_datasource' AND column_name = 'password_enc');
SET @sql := IF(@c = 0, 'ALTER TABLE cat_datasource ADD COLUMN password_enc VARCHAR(500) NULL COMMENT ''连接密码(加密存储)''', 'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 3) 物理目录菜单加"数据源管理"权限点(按钮)
SET @pid_catalog := (SELECT id FROM platform_menu WHERE route_name = 'SemanticCatalog' LIMIT 1);
SET @exists_manage := (SELECT COUNT(1) FROM platform_menu WHERE permission = 'module_semantic:catalog:manage');
INSERT INTO platform_menu (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,`hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'数据源管理',3,3,'module_semantic:catalog:manage','数据源管理',0,1,0,0,'pc',0,0,0,'tenant',0,@pid_catalog
FROM DUAL WHERE @exists_manage = 0 AND @pid_catalog IS NOT NULL;

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id FROM sys_role r
JOIN platform_menu m ON m.permission = 'module_semantic:catalog:manage'
WHERE r.code IN ('SUPER_ADMIN','ADMIN') AND @exists_manage = 0 AND @pid_catalog IS NOT NULL;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE permission='module_semantic:catalog:manage') t);
-- DELETE FROM platform_menu WHERE permission='module_semantic:catalog:manage';
-- ALTER TABLE cat_datasource DROP COLUMN username, DROP COLUMN password_enc;
