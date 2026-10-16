-- 删掉「语义总览」菜单行 —— 它指向一个本分支不存在的页面
-- 幂等：按 route_name / permission 判重删除，重复执行无副作用。
-- ★ 注释里不要写分号（按分号切语句的执行器会在注释中间截断，本目录下已踩过两次）
--
-- ## 为什么删
--
-- 菜单里有 route_name='SemanticOverview' → component_path='module_semantic/overview/index'，
-- 但本分支 views/module_semantic/overview/ 目录不存在。点它就是白屏 +
-- 控制台 `[ComponentLoader] 未找到组件`。这与最近几轮修的是同一类缺陷：
-- **系统摆出一个能点的入口，点了却做不成事**。
--
-- ## 这行是怎么来的
--
-- 页面与其补丁 `2026-07-22-semantic-overview-menu.sql` 都只存在于
-- codex/semantic-frontend-redesign 分支（未合入本分支，两边已分叉 50/68 个提交）。
-- 有人在那条分支上跑过该补丁，于是 dev 库留下了菜单行，而页面没跟过来。
--
-- 核实过：`platform_menu.json` 种子里**没有**这一行，所以删掉不会被新库重建。
--
-- ## 为什么不是"把页面补上"
--
-- 那个页面在原分支上是完整的（index.vue 774 行 + overview.ts + 2 个 spec），
-- 但它依赖那条分支的一整套设计系统：SemanticPageHeader / SemanticMetricCard /
-- SemanticHealthRing / SemanticDetailPanel / SemanticStatusTag / SemanticEmptyState /
-- SemanticLoadingSkeleton + types.ts + useSemanticPermissions + useLatestRequest —— 本分支
-- 一个都没有。为一个页面把 20+ 个共享组件搬过来，等于在这轮里顺手做掉一次前端重构。
--
-- 故：先摘掉这个会白屏的入口（本补丁），页面留作独立任务 —— 要么合那条分支，
-- 要么用现有组件重写一个简版。恢复时把菜单行和页面**一起**上，别再只上一半。

DELETE FROM sys_role_menus
WHERE menu_id IN (
  SELECT id FROM (
    SELECT id FROM platform_menu
    WHERE route_name = 'SemanticOverview'
       OR permission = 'module_semantic:overview:list'
  ) t
);

DELETE FROM platform_menu
WHERE route_name = 'SemanticOverview'
   OR permission = 'module_semantic:overview:list';

-- ↓↓↓ 回滚（仅在**页面也一并到位**时才执行）↓↓↓
-- 见 codex/semantic-frontend-redesign 分支的 2026-07-22-semantic-overview-menu.sql
