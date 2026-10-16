-- 阶段2 L2 地基: 语义中心下新增「业务域」「业务实体」菜单 + 权限点（现有库补丁）
-- 新库由 platform_menu.json 种子自动建。
-- 幂等: 按 route_name='SemanticEntity' 判断是否已存在,存在则整段跳过。
-- 前置: 依赖阶段1 的「语义中心」父节点(route_name='Semantic')已存在。
-- 注意: platform_menu 继承 ModelMixin(无 tenant_id)。uuid/is_deleted/created_time/updated_time NOT NULL 需显式提供。

SET @exists_l2 := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'SemanticEntity');
SET @pid_semantic := (SELECT id FROM platform_menu WHERE route_name = 'Semantic' LIMIT 1);

-- 菜单：业务域
INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,`route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'业务域',2,1,'module_semantic:domain:list','ri:apps-2-line','SemanticDomain','domain','module_semantic/domain/index',0,1,0,'业务域',0,'pc',0,0,0,'tenant',0,'业务域基础数据',@pid_semantic
FROM DUAL WHERE @exists_l2 = 0 AND @pid_semantic IS NOT NULL;
SET @pid_domain := LAST_INSERT_ID();

INSERT INTO platform_menu (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,`hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,@pid_domain FROM (
  SELECT '查询' AS name, 1 AS ord, 'module_semantic:domain:list' AS perm UNION ALL
  SELECT '详情', 2, 'module_semantic:domain:detail' UNION ALL
  SELECT '新增', 3, 'module_semantic:domain:create' UNION ALL
  SELECT '编辑', 4, 'module_semantic:domain:update' UNION ALL
  SELECT '删除', 5, 'module_semantic:domain:delete' UNION ALL
  SELECT '启停', 6, 'module_semantic:domain:patch'
) v WHERE @exists_l2 = 0 AND @pid_semantic IS NOT NULL;

-- 菜单：业务实体
INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,`route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'业务实体',2,2,'module_semantic:entity:list','ri:table-line','SemanticEntity','entity','module_semantic/entity/index',0,1,0,'业务实体',0,'pc',0,0,0,'tenant',0,'业务实体与业务字段',@pid_semantic
FROM DUAL WHERE @exists_l2 = 0 AND @pid_semantic IS NOT NULL;
SET @pid_entity := LAST_INSERT_ID();

INSERT INTO platform_menu (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,`hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,@pid_entity FROM (
  SELECT '查询' AS name, 1 AS ord, 'module_semantic:entity:list' AS perm UNION ALL
  SELECT '详情', 2, 'module_semantic:entity:detail' UNION ALL
  SELECT '新增', 3, 'module_semantic:entity:create' UNION ALL
  SELECT '编辑', 4, 'module_semantic:entity:update' UNION ALL
  SELECT '删除', 5, 'module_semantic:entity:delete' UNION ALL
  SELECT '状态流转', 6, 'module_semantic:entity:status'
) v WHERE @exists_l2 = 0 AND @pid_semantic IS NOT NULL;

-- 挂给内置管理员角色
INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (
  m.route_name IN ('SemanticDomain','SemanticEntity')
  OR m.permission LIKE 'module_semantic:domain:%'
  OR m.permission LIKE 'module_semantic:entity:%'
)
WHERE r.code IN ('SUPER_ADMIN','ADMIN')
  AND @exists_l2 = 0 AND @pid_semantic IS NOT NULL;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name IN ('SemanticDomain','SemanticEntity') OR permission LIKE 'module_semantic:domain:%' OR permission LIKE 'module_semantic:entity:%') t);
-- DELETE FROM platform_menu WHERE permission LIKE 'module_semantic:domain:%' OR permission LIKE 'module_semantic:entity:%';
-- DELETE FROM platform_menu WHERE route_name IN ('SemanticDomain','SemanticEntity');
