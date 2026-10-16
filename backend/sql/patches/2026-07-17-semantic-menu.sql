-- 语义中心菜单 + 权限点（现有库补丁；新库由 platform_menu.json 种子自动建，无需执行本文件）
-- 关联需求: 阶段1 语义模型管理(指标/维度)。新增一级目录「语义中心」+ 指标管理/维度管理两个菜单 + 各自按钮权限点。
-- 幂等: 按 route_name='Semantic' 判断是否已存在,存在则整段跳过(重复执行安全)。
-- 注意: platform_menu 继承 ModelMixin(无 tenant_id)。uuid/is_deleted/created_time/updated_time 均为 NOT NULL
--       且默认值在 Python 层生成, 故本 SQL 必须显式提供(uuid 用 MySQL UUID(), 时间用 NOW(), is_deleted=0)。

SET @exists_semantic := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'Semantic');

-- 一级目录：语义中心
INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,`route_name`,`route_path`,`component_path`,`redirect`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'语义中心',1,3,NULL,'ri:database-2-line','Semantic','/semantic',NULL,'/semantic/indicator',0,1,0,'语义中心',0,'pc',0,0,0,'tenant',0,'语义模型与知识管理',NULL
FROM DUAL WHERE @exists_semantic = 0;
SET @pid_semantic := LAST_INSERT_ID();

-- 菜单：指标管理
INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,`route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'指标管理',2,4,'module_semantic:indicator:list','ri:bar-chart-box-line','SemanticIndicator','indicator','module_semantic/indicator/index',0,1,0,'指标管理',0,'pc',0,0,0,'tenant',0,'语义指标管理',@pid_semantic
FROM DUAL WHERE @exists_semantic = 0;
SET @pid_indicator := LAST_INSERT_ID();

-- 指标按钮权限
INSERT INTO platform_menu (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,`hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,@pid_indicator FROM (
  SELECT '查询' AS name, 1 AS ord, 'module_semantic:indicator:list' AS perm UNION ALL
  SELECT '详情', 2, 'module_semantic:indicator:detail' UNION ALL
  SELECT '新增', 3, 'module_semantic:indicator:create' UNION ALL
  SELECT '编辑', 4, 'module_semantic:indicator:update' UNION ALL
  SELECT '删除', 5, 'module_semantic:indicator:delete' UNION ALL
  SELECT '状态流转', 6, 'module_semantic:indicator:status' UNION ALL
  SELECT '口径审核', 7, 'module_semantic:indicator:review'
) v WHERE @exists_semantic = 0;

-- 菜单：维度管理
INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,`route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'维度管理',2,5,'module_semantic:dimension:list','ri:node-tree','SemanticDimension','dimension','module_semantic/dimension/index',0,1,0,'维度管理',0,'pc',0,0,0,'tenant',0,'语义维度管理',@pid_semantic
FROM DUAL WHERE @exists_semantic = 0;
SET @pid_dimension := LAST_INSERT_ID();

-- 维度按钮权限
INSERT INTO platform_menu (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,`hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,@pid_dimension FROM (
  SELECT '查询' AS name, 1 AS ord, 'module_semantic:dimension:list' AS perm UNION ALL
  SELECT '详情', 2, 'module_semantic:dimension:detail' UNION ALL
  SELECT '新增', 3, 'module_semantic:dimension:create' UNION ALL
  SELECT '编辑', 4, 'module_semantic:dimension:update' UNION ALL
  SELECT '删除', 5, 'module_semantic:dimension:delete' UNION ALL
  SELECT '状态流转', 6, 'module_semantic:dimension:status'
) v WHERE @exists_semantic = 0;

-- 把语义中心整棵子树挂给内置管理员角色(超管通常自动放行,此处覆盖普通管理员角色)
INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (
  m.route_name IN ('Semantic','SemanticIndicator','SemanticDimension')
  OR m.permission LIKE 'module_semantic:%'
)
WHERE r.code IN ('SUPER_ADMIN','ADMIN')
  AND @exists_semantic = 0;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name IN ('Semantic','SemanticIndicator','SemanticDimension') OR permission LIKE 'module_semantic:%') t);
-- DELETE FROM platform_menu WHERE permission LIKE 'module_semantic:%';
-- DELETE FROM platform_menu WHERE route_name IN ('SemanticIndicator','SemanticDimension');
-- DELETE FROM platform_menu WHERE route_name = 'Semantic';
