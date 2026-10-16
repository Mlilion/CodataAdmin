-- 语义模型「授权管理」权限点（闭环三：让用户隔离能经 UI 启用，不必写 SQL）
-- 幂等：按 permission 判重；重复执行不产生重复行。
-- ★ 注释里不要写分号 —— 按分号切语句的执行器会在注释中间切断（2026-08-01 踩到过，
--   本行自己也踩过一次：原文写了个分号，导致下面第一条 SET 从未执行）。
--
-- 接口:
--   GET    /semantic/model/grant/list          → module_semantic:model:grant
--   POST   /semantic/model/grant/add/{id}      → module_semantic:model:grant
--   DELETE /semantic/model/grant/revoke/{id}   → module_semantic:model:grant
--
-- ★★ 为什么不沿用 module_semantic:model:update
--
-- 授权是**改访问控制**，与改模型名称/描述不是同一件事。若沿用 update，
-- "能编辑模型的人"就顺带能给自己或他人开通受限模型的可见性 ——
-- 等于让访问控制的门由被它保护的人自己开。故单开一个权限点。
--
-- 只发给 SUPER_ADMIN / ADMIN。建模人员（若有独立角色）默认拿不到，需管理员显式授予。

SET @pid_model := (SELECT id FROM platform_menu WHERE route_name = 'SemanticModel' LIMIT 1);

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,
   `hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,
   `scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,v.descr,@pid_model
FROM (
  SELECT '授权管理' AS name, 6 AS ord, 'module_semantic:model:grant' AS perm,
         '管理受限模型的可见用户(MCP 用户隔离)' AS descr
) v
WHERE @pid_model IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM (SELECT permission FROM platform_menu) p WHERE p.permission = v.perm);

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON m.permission = 'module_semantic:model:grant'
WHERE r.code IN ('SUPER_ADMIN','ADMIN');

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE permission = 'module_semantic:model:grant') t);
-- DELETE FROM platform_menu WHERE permission = 'module_semantic:model:grant';
