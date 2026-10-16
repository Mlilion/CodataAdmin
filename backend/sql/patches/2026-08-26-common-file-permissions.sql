-- 通用文件上传/下载权限点
-- 背景: /common/file/upload 与 /common/file/download 有 AuthPermission 守卫,
-- 但种子菜单没有 module_common:file:* 权限行, 普通角色无处授权。
-- 本补丁只加隐藏按钮权限, 不新增可见页面。

SET @pid_system := (SELECT id FROM platform_menu WHERE route_name = 'System' AND is_deleted = 0 LIMIT 1);

INSERT INTO platform_menu
  (`uuid`,`is_deleted`,`created_time`,`updated_time`,`name`,`type`,`order`,`permission`,`title`,
   `hidden`,`keep_alive`,`always_show`,`affix`,`client`,`is_iframe`,`is_hide_tab`,`show_badge`,
   `scope`,`status`,`description`,`parent_id`)
SELECT UUID(),0,NOW(),NOW(),v.name,3,v.ord,v.perm,v.name,1,1,0,0,'pc',0,0,0,'tenant',0,v.descr,@pid_system
FROM (
  SELECT '通用文件上传' AS name, 98 AS ord, 'module_common:file:upload' AS perm, '公共文件上传接口权限' AS descr UNION ALL
  SELECT '通用文件下载', 99, 'module_common:file:download', '公共文件下载接口权限'
) v
WHERE @pid_system IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM (SELECT permission FROM platform_menu WHERE is_deleted = 0) p WHERE p.permission = v.perm);

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON m.permission IN ('module_common:file:upload', 'module_common:file:download')
WHERE r.code IN ('SUPER_ADMIN','ADMIN')
  AND m.is_deleted = 0;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE FROM sys_role_menus WHERE menu_id IN (SELECT id FROM (SELECT id FROM platform_menu WHERE permission IN ('module_common:file:upload', 'module_common:file:download')) t);
-- DELETE FROM platform_menu WHERE permission IN ('module_common:file:upload', 'module_common:file:download');
