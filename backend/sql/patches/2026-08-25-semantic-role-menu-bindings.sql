-- 补齐语义中心角色菜单关系（现有库修复）
-- 背景：早期语义菜单补丁在菜单已存在时会跳过插入逻辑，导致 sys_role_menus 没有绑定。
-- 结果：非超管账号拿不到 /semantic 菜单树，前端权限校验会把 /semantic/* 导回首页。
-- 幂等：INSERT IGNORE 依赖 sys_role_menus(role_id, menu_id) 主键，可重复执行。

INSERT IGNORE INTO sys_role_menus (role_id, menu_id)
SELECT r.id, m.id
FROM sys_role r
JOIN platform_menu m ON (
  m.route_name LIKE 'Semantic%'
  OR m.permission LIKE 'module_semantic:%'
)
WHERE r.code IN ('SUPER_ADMIN', 'ADMIN')
  AND m.is_deleted = 0;

-- ↓↓↓ 回滚 ↓↓↓
-- DELETE rm
-- FROM sys_role_menus rm
-- JOIN sys_role r ON r.id = rm.role_id
-- JOIN platform_menu m ON m.id = rm.menu_id
-- WHERE r.code IN ('SUPER_ADMIN', 'ADMIN')
--   AND (m.route_name LIKE 'Semantic%' OR m.permission LIKE 'module_semantic:%');
