-- P3 R5: 检索副库运维权限点（无独立菜单页，挂在「语义模型」下作按钮）
-- 幂等：按 permission 判重；重复执行不产生重复行。
--
-- 接口:
--   POST /semantic/retrieval/search   → module_semantic:retrieval:search
--   GET  /semantic/retrieval/status   → module_semantic:retrieval:detail
--   POST /semantic/retrieval/rebuild  → module_semantic:retrieval:rebuild
--
-- 为什么不建独立菜单：检索索引是后台机制，日常无需人工干预（发布即自动索引）。
-- rebuild 是索引漂移时的补救手段，作按钮挂在语义模型页即可。

SET @pid_model := (SELECT id FROM platform_menu WHERE route_name = 'SemanticModel' LIMIT 1);
-- 逐条判重(而非整批): 便于后续追加权限点时复跑本补丁
INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,
   `hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,
   `scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,v.descr,@pid_model
FROM (
  SELECT '语义检索' AS name, 9 AS ord, 'module_semantic:retrieval:search' AS perm, '三级递进检索(精确/模糊/全文)' AS descr UNION ALL
  SELECT '检索索引状态', 10, 'module_semantic:retrieval:detail', '查看检索副库开关/记录数/待生成向量数' UNION ALL
  SELECT '重建检索索引', 11, 'module_semantic:retrieval:rebuild', '重建全部已发布模型的检索索引(幂等)'
) v
WHERE @pid_model IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM (SELECT permission FROM platform_menu) p WHERE p.permission = v.perm);

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON m.permission LIKE 'module_semantic:retrieval:%'
WHERE r.code IN ('SUPER_ADMIN','ADMIN');

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE permission LIKE 'module_semantic:retrieval:%') t);
-- DELETE FROM platform_menu WHERE permission LIKE 'module_semantic:retrieval:%';
