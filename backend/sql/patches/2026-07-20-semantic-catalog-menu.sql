-- 阶段3 L1 物理目录: 语义中心下新增「物理目录」菜单 + 权限点（现有库补丁）
-- 新库由 platform_menu.json 种子自动建。幂等: 按 route_name='SemanticCatalog' 判断。
-- 前置: 依赖「语义中心」父节点(route_name='Semantic')已存在。
-- 注意: platform_menu 继承 ModelMixin(无 tenant_id)。uuid/is_deleted/created_time/updated_time NOT NULL 需显式提供。

SET @exists_cat := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'SemanticCatalog');
SET @pid_semantic := (SELECT id FROM platform_menu WHERE route_name = 'Semantic' LIMIT 1);

-- 菜单：物理目录
INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,`route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'物理目录',2,0,'module_semantic:catalog:list','ri:stack-line','SemanticCatalog','catalog','module_semantic/catalog/index',0,1,0,'物理目录',0,'pc',0,0,0,'tenant',0,'物理库/表/列(从数据目录同步)',@pid_semantic
FROM DUAL WHERE @exists_cat = 0 AND @pid_semantic IS NOT NULL;
SET @pid_catalog := LAST_INSERT_ID();

INSERT INTO platform_menu (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,`hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,@pid_catalog FROM (
  SELECT '查询' AS name, 1 AS ord, 'module_semantic:catalog:list' AS perm UNION ALL
  SELECT '同步', 2, 'module_semantic:catalog:sync'
) v WHERE @exists_cat = 0 AND @pid_semantic IS NOT NULL;

-- 挂给内置管理员角色
INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (m.route_name = 'SemanticCatalog' OR m.permission LIKE 'module_semantic:catalog:%')
WHERE r.code IN ('SUPER_ADMIN','ADMIN')
  AND @exists_cat = 0 AND @pid_semantic IS NOT NULL;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name='SemanticCatalog' OR permission LIKE 'module_semantic:catalog:%') t);
-- DELETE FROM platform_menu WHERE permission LIKE 'module_semantic:catalog:%';
-- DELETE FROM platform_menu WHERE route_name = 'SemanticCatalog';
