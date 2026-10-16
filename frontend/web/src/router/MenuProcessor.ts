import type { AppRouteRecord, RouteMeta } from "@/types/router";
import type { UserInfo } from "@/api/module_system/user";
import type { MenuTable } from "@/api/module_platform/menu";
import { useUserStore } from "@stores";
import { useAppMode } from "@/hooks/core/useAppMode";

import {
  mergeAppRouteRecords,
  ROUTE_COMPONENT_LAYOUT,
  ROUTE_COMPONENT_NESTED_PARENT,
} from "./staticRoutes";
import { MenuTypeEnum } from "@/enums/system/menu.enum";

/**
 * 菜单 → `AppRouteRecord`：后端 `MenuTable`、前端内置路由、混合模式合并；供守卫注册动态路由。
 * `getMenuList` 依 `useAppMode` 分支；meta 对齐后端 keep_alive、目录占位组件。
 */

/** 前端模式并入菜单的内置路由（扩展点，默认空） */
export const builtinFrontendRoutes: AppRouteRecord[] = [];

function normalizeMenuNestedPaths(items: MenuTable[], parentAbsolutePath = ""): MenuTable[] {
  return items.map((node) => {
    const raw = (node.route_path ?? "").trim();
    // 计算当前节点的绝对路径传给子节点递归使用
    const canonical = raw
      ? raw.startsWith("/")
        ? raw
        : parentAbsolutePath
          ? joinAbsolutePath(parentAbsolutePath, raw)
          : `/${raw}`
      : parentAbsolutePath;

    const children = node.children?.length
      ? normalizeMenuNestedPaths(node.children, canonical)
      : undefined;
    return { ...node, children };
  });
}

function joinAbsolutePath(parentAbs: string, segmentPath: string): string {
  const seg = segmentPath.replace(/^\/+/, "");
  const base = parentAbs.replace(/\/$/, "");
  if (!seg) return base;
  return `${base}/${seg}`;
}

function normalizeAppRouteChildPaths(
  routes: AppRouteRecord[],
  parentAbsolutePath = ""
): AppRouteRecord[] {
  return routes.map((route) => {
    const path = (route.path ?? "").trim();

    if (/^https?:\/\//i.test(path)) {
      return {
        ...route,
        children: route.children?.length
          ? normalizeAppRouteChildPaths(route.children, parentAbsolutePath)
          : route.children,
      };
    }

    const currentAbs = parentAbsolutePath
      ? joinAbsolutePath(parentAbsolutePath, path)
      : path.startsWith("/")
        ? path
        : `/${path}`;

    const children = route.children?.length
      ? normalizeAppRouteChildPaths(route.children, currentAbs)
      : route.children;

    return { ...route, children };
  });
}

function toComponentImportPath(componentPath: string): string {
  const t = componentPath.trim().replace(/^\/+/, "");
  return t ? `/${t}` : "";
}

function mapMenuNode(item: MenuTable, depth = 0): AppRouteRecord {
  const childrenRaw = item.children?.filter((c) => c.type !== MenuTypeEnum.BUTTON) ?? [];
  const children = childrenRaw.length
    ? childrenRaw.map((c) => mapMenuNode(c, depth + 1))
    : undefined;

  const path = (item.route_path ?? "").trim();
  const name = item.route_name || undefined;
  const redirect = item.redirect?.trim() || undefined;

  const hasKids = !!(children && children.length > 0);
  const isDirectory = item.type === MenuTypeEnum.CATALOG;

  let component: string | undefined;
  if (isDirectory || (hasKids && !(item.component_path ?? "").trim())) {
    component = depth === 0 ? ROUTE_COMPONENT_LAYOUT : ROUTE_COMPONENT_NESTED_PARENT;
  } else if ((item.component_path ?? "").trim()) {
    component = toComponentImportPath(item.component_path!);
  }

  const meta: RouteMeta = {
    title: item.title ?? "",
    icon: item.icon || undefined,
    hidden: !!item.hidden,
    keepAlive: item.keep_alive ?? true,
    affix: !!item.affix,
    fixedTab: !!item.affix,
    alwaysShow: !!item.always_show,
    isHide: !!item.hidden,
    isHideTab: !!item.is_hide_tab,
    link: item.link || undefined,
    isIframe: !!item.is_iframe,
    activePath: item.active_path || undefined,
    showBadge: !!item.show_badge,
    showTextBadge: item.show_text_badge || undefined,
    client: item.client,
  };

  return {
    path,
    name,
    component,
    redirect,
    meta,
    children,
  };
}

function backendMenusToAppRoutes(menus: MenuTable[]): AppRouteRecord[] {
  const roots = menus.filter((m) => m.type !== MenuTypeEnum.BUTTON);
  const normalized = normalizeMenuNestedPaths(roots);
  const mapped = normalized.map((m) => mapMenuNode(m, 0));
  return normalizeAppRouteChildPaths(mapped);
}

export class MenuProcessor {
  async getMenuList(): Promise<AppRouteRecord[]> {
    const { isFrontendMode, isMixedMenuMode } = useAppMode();

    let menuList: AppRouteRecord[];
    if (isMixedMenuMode.value) {
      menuList = await this.processMixedMenu();
    } else if (isFrontendMode.value) {
      menuList = await this.processFrontendMenu();
    } else {
      menuList = await this.processBackendMenu();
    }

    return this.normalizeMenuPaths(menuList);
  }

  private async processFrontendMenu(): Promise<AppRouteRecord[]> {
    const userStore = useUserStore();
    let menuList = [...builtinFrontendRoutes];

    if (userStore.info?.is_superuser) {
      return this.filterEmptyMenus(menuList);
    }

    const roles = userStore.info?.roles;

    if (roles && roles.length > 0) {
      const roleCodes = this.extractRoleCodesFromUserRoles(roles);
      if (roleCodes.length > 0) {
        menuList = this.filterMenuByRoles(menuList, roleCodes);
      }
    }

    return this.filterEmptyMenus(menuList);
  }

  private extractRoleCodesFromUserRoles(roles: NonNullable<UserInfo["roles"]>): string[] {
    const codes = new Set<string>();
    for (const role of roles) {
      const r = role as { code?: string; name?: string };
      const c = r.code?.trim();
      if (c) codes.add(c);
      const n = r.name?.trim();
      if (n && /^R_[A-Z0-9_]+$/i.test(n)) codes.add(n);
    }
    return Array.from(codes);
  }

  private async processMixedMenu(): Promise<AppRouteRecord[]> {
    let backend: AppRouteRecord[] = [];
    try {
      backend = await this.processBackendMenu();
    } catch (e) {
      console.warn("[MenuProcessor] mixed：后端菜单获取失败，本次仅挂载前端路由", e);
    }
    const frontend = await this.processFrontendMenu();
    const merged = mergeAppRouteRecords(backend, frontend);
    return this.filterEmptyMenus(merged);
  }

  /** 优先用用户信息里附带的 `menus`，与守卫拉用户信息顺序一致，避免重复打菜单树接口 */
  private async processBackendMenu(): Promise<AppRouteRecord[]> {
    const userStore = useUserStore();
    const fromUser = userStore.routeList;
    if (Array.isArray(fromUser) && fromUser.length > 0) {
      const routes = backendMenusToAppRoutes(fromUser);
      return this.filterEmptyMenus(routes);
    }
    return [];
  }

  private filterMenuByRoles(menu: AppRouteRecord[], roleCodes: string[]): AppRouteRecord[] {
    return menu.reduce((acc: AppRouteRecord[], item) => {
      const itemRoles = item.meta?.roles;
      const hasPermission = !itemRoles || itemRoles.some((role) => roleCodes?.includes(role));

      if (hasPermission) {
        const filteredItem = { ...item };
        if (filteredItem.children?.length) {
          filteredItem.children = this.filterMenuByRoles(filteredItem.children, roleCodes);
        }
        acc.push(filteredItem);
      }

      return acc;
    }, []);
  }

  private filterEmptyMenus(menuList: AppRouteRecord[]): AppRouteRecord[] {
    return menuList
      .map((item) => {
        if (item.children && item.children.length > 0) {
          const filteredChildren = this.filterEmptyMenus(item.children);
          return {
            ...item,
            children: filteredChildren,
          };
        }
        return item;
      })
      .filter((item) => {
        if ("children" in item) {
          return true;
        }

        if (item.meta?.isIframe === true || item.meta?.link) {
          return true;
        }

        if (item.component && item.component !== "" && item.component !== ROUTE_COMPONENT_LAYOUT) {
          return true;
        }

        return false;
      });
  }

  validateMenuList(menuList: AppRouteRecord[]): boolean {
    return Array.isArray(menuList) && menuList.length > 0;
  }

  private normalizeMenuPaths(menuList: AppRouteRecord[], parentPath = ""): AppRouteRecord[] {
    return menuList.map((item) => {
      const fullPath = this.buildFullPath(item.path || "", parentPath);

      const children = item.children?.length
        ? this.normalizeMenuPaths(item.children, fullPath)
        : item.children;

      const redirect = item.redirect || this.resolveDefaultRedirect(children);

      return {
        ...item,
        path: fullPath,
        redirect,
        children,
      };
    });
  }

  private resolveDefaultRedirect(children?: AppRouteRecord[]): string | undefined {
    if (!children?.length) {
      return undefined;
    }

    for (const child of children) {
      if (this.isNavigableRoute(child)) {
        return child.path;
      }

      const nestedRedirect = this.resolveDefaultRedirect(child.children);
      if (nestedRedirect) {
        return nestedRedirect;
      }
    }

    return undefined;
  }

  private isNavigableRoute(route: AppRouteRecord): boolean {
    return Boolean(
      route.path &&
      route.path !== "/" &&
      !route.meta?.link &&
      route.meta?.isIframe !== true &&
      route.component &&
      route.component !== ""
    );
  }

  private buildFullPath(path: string, parentPath: string): string {
    if (!path) return "";

    if (path.startsWith("http://") || path.startsWith("https://")) {
      return path;
    }

    if (path.startsWith("/")) {
      return path;
    }

    if (parentPath) {
      const cleanParent = parentPath.replace(/\/$/, "");
      const cleanChild = path.replace(/^\//, "");
      return `${cleanParent}/${cleanChild}`;
    }

    return `/${path}`;
  }
}
