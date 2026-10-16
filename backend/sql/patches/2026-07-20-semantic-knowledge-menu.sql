-- 阶段5 L6: 语义中心下新增「知识库」菜单 + 权限点（现有库补丁）
-- 新库由 platform_menu.json 种子自动建。幂等: 按 route_name='SemanticKnowledge' 判断。
-- 前置: 依赖「语义中心」父节点(route_name='Semantic')已存在。

SET @exists_kb := (SELECT COUNT(1) FROM platform_menu WHERE route_name = 'SemanticKnowledge');
SET @pid_semantic := (SELECT id FROM platform_menu WHERE route_name = 'Semantic' LIMIT 1);

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`icon`,`route_name`,`route_path`,`component_path`,`hidden`,`keep_alive`,`always_show`,`title`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),'知识库',2,6,'module_semantic:knowledge:list','ri:book-2-line','SemanticKnowledge','knowledge','module_semantic/knowledge/index',0,1,0,'知识库',0,'pc',0,0,0,'tenant',0,'业务知识与使用说明',@pid_semantic
FROM DUAL WHERE @exists_kb = 0 AND @pid_semantic IS NOT NULL;
SET @pid_kb := LAST_INSERT_ID();

INSERT INTO platform_menu (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,`hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,`scope`,`status`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,@pid_kb FROM (
  SELECT '查询' AS name, 1 AS ord, 'module_semantic:knowledge:list' AS perm UNION ALL
  SELECT '详情', 2, 'module_semantic:knowledge:detail' UNION ALL
  SELECT '新增', 3, 'module_semantic:knowledge:create' UNION ALL
  SELECT '编辑', 4, 'module_semantic:knowledge:update' UNION ALL
  SELECT '删除', 5, 'module_semantic:knowledge:delete' UNION ALL
  SELECT '状态流转', 6, 'module_semantic:knowledge:status'
) v WHERE @exists_kb = 0 AND @pid_semantic IS NOT NULL;

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (m.route_name = 'SemanticKnowledge' OR m.permission LIKE 'module_semantic:knowledge:%')
WHERE r.code IN ('SUPER_ADMIN','ADMIN')
  AND @exists_kb = 0 AND @pid_semantic IS NOT NULL;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE route_name='SemanticKnowledge' OR permission LIKE 'module_semantic:knowledge:%') t);
-- DELETE FROM platform_menu WHERE permission LIKE 'module_semantic:knowledge:%';
-- DELETE FROM platform_menu WHERE route_name = 'SemanticKnowledge';
