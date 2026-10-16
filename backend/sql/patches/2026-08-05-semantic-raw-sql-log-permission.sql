-- 裸 SQL 审计的菜单与权限点
-- 幂等: 先判存在再插。
-- ★ 本文件注释里不要写分号 —— 按分号切语句的执行器会在字符串中间切断(2026-08-01 踩过)
--   连"提醒别写分号"这句本身也不能写那个字符, 否则它自己就是那个 bug。
--
-- ★★ 为什么不复用 module_semantic:querylog:* —— 审计含 **SQL 原文**, 能看它的人等于
--    能看到查询意图与表结构。并进 querylog:list 等于给"看同义词候选"的人顺带发了
--    "看别人查了什么"的权限。与 model:grant 和 model:update 分开是同一原则:
--    授权不该由相邻权限顺带获得。
--
-- 本补丁只加**权限点**(挂在同义词治理菜单下作按钮), 不新建页面菜单行 ——
-- 审计是治理页的一个 Tab, 不是独立页面。

SET @pid_ql := (SELECT id FROM platform_menu WHERE route_name = 'SemanticQueryLog' AND is_deleted = 0 LIMIT 1);

SET @exists_raw := (SELECT COUNT(1) FROM platform_menu WHERE permission = 'module_semantic:rawsqllog:list' AND is_deleted = 0);

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,
   `hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,
   `scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,0,1,0,0,'pc',0,0,0,'tenant',0,v.descr,@pid_ql
FROM (
  SELECT '查看裸 SQL 审计' AS name, 3 AS ord, 'module_semantic:rawsqllog:list' AS perm, '看 execute_sql 的调用留痕(含 SQL 原文, 故与同义词候选分权限)' AS descr UNION ALL
  SELECT '清理裸 SQL 审计', 4, 'module_semantic:rawsqllog:purge', '软删超期审计(默认留 90 天)'
) v
WHERE @exists_raw = 0 AND @pid_ql IS NOT NULL;

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON m.permission LIKE 'module_semantic:rawsqllog:%'
WHERE r.code IN ('SUPER_ADMIN','ADMIN')
  AND @pid_ql IS NOT NULL;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE permission LIKE 'module_semantic:rawsqllog:%') t);
-- DELETE FROM platform_menu WHERE permission LIKE 'module_semantic:rawsqllog:%';
