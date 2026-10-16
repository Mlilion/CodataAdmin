/**
 * v-auth 权限指令
 *
 * 基于权限标识控制 DOM 元素的显示和隐藏。
 * 权限检查优先级：is_superuser > ROLE_ROOT > userStore.prems > route.meta.authList
 * 如果用户没有对应权限，元素将从 DOM 中移除。
 *
 * ## 使用示例
 *
 * ```vue
 * <el-button v-auth="'module_system:user:create'">新增</el-button>
 * <el-button v-auth="'module_system:user:update'">编辑</el-button>
 * <el-button v-auth="'module_platform:order:create'">创建订单</el-button>
 * ```
 *
 * @module directives/auth
 */

import { router } from "@/router";
import { useUserStore } from "@stores";
import { ROLE_ROOT } from "@/constants";
import { App, Directive, DirectiveBinding } from "vue";

export type AuthDirective = Directive<HTMLElement, string>;

function hasPermission(auth: string): boolean {
  if (!auth) return true;

  const userStore = useUserStore();

  // 超管直接放行
  if ((userStore.basicInfo as Record<string, any>)?.is_superuser) return true;

  // ROLE_ROOT 角色放行
  const roles = (userStore.basicInfo as Record<string, any>)?.roles as
    | { code?: string }[]
    | undefined;
  if (roles?.some((r) => r.code === ROLE_ROOT)) return true;

  // 通配符
  if (userStore.prems.includes("*:*:*")) return true;

  // 检查 userStore.prems（来自菜单树汇总的全局权限码）
  if (userStore.prems.includes(auth)) return true;

  // 兼容：检查 route.meta.authList
  const authList = (router.currentRoute.value.meta.authList as Array<{ authMark: string }>) || [];
  if (authList.some((item) => item.authMark === auth)) return true;

  return false;
}

function checkAuthPermission(el: HTMLElement, binding: DirectiveBinding<string>): void {
  if (!hasPermission(binding.value)) {
    removeElement(el);
  }
}

function removeElement(el: HTMLElement): void {
  if (el.parentNode) {
    el.parentNode.removeChild(el);
  }
}

const authDirective: AuthDirective = {
  mounted: checkAuthPermission,
  updated: checkAuthPermission,
};

export function setupAuthDirective(app: App): void {
  app.directive("auth", authDirective);
}
