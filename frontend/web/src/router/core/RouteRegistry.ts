/**
 * 路由注册表 —— 校验通过后批量 addRoute，支持注册/注销/重入判断。
 */
import type { RouteRecordRaw, Router } from "vue-router";
import type { AppRouteRecord } from "@/types/router";
import { ComponentLoader } from "./ComponentLoader";
import { RouteValidator } from "./RouteValidator";
import { RouteTransformer } from "./RouteTransformer";
import { ROOT_LAYOUT_ROUTE_NAME } from "../staticRoutes";

/** 与静态壳层冲突的一级 path 段，动态注册时跳过以免覆盖首页 / 仪表盘等 */
const RESERVED_SHELL_SEGMENTS = new Set(["home", "profile", "changelog", "dashboard"]);

function pathFirstSegment(path: string): string {
  return (
    path
      .trim()
      .replace(/^\/+|\/+$/g, "")
      .split("/")
      .filter(Boolean)[0] ?? ""
  );
}

function registrationName(route: AppRouteRecord, index: number): AppRouteRecord {
  const explicit = typeof route.name === "string" ? route.name.trim() : "";
  if (explicit) return route;
  const seg = pathFirstSegment(route.path || "") || "route";
  const safe = seg.replace(/[^\w]/g, "_");
  return { ...route, name: `Dyn_${index}_${safe}` };
}

function routeNameKey(route: AppRouteRecord): string {
  return typeof route.name === "string" ? route.name : String(route.name ?? "");
}

export class RouteRegistry {
  private router: Router;
  private componentLoader: ComponentLoader;
  private validator: RouteValidator;
  private transformer: RouteTransformer;
  private removeRouteFns: (() => void)[] = [];
  private registered = false;

  constructor(router: Router) {
    this.router = router;
    this.componentLoader = new ComponentLoader();
    this.validator = new RouteValidator();
    this.transformer = new RouteTransformer(this.componentLoader, { shellChild: true });
  }

  register(menuList: AppRouteRecord[]): void {
    if (this.registered) {
      console.warn("[RouteRegistry] 路由已注册，跳过重复注册");
      return;
    }

    const validationResult = this.validator.validate(menuList);
    if (!validationResult.valid) {
      throw new Error(`路由配置验证失败: ${validationResult.errors.join(", ")}`);
    }

    const removeRouteFns: (() => void)[] = [];

    menuList.forEach((route, index) => {
      const seg = pathFirstSegment(route.path || "");
      if (seg && RESERVED_SHELL_SEGMENTS.has(seg)) {
        return;
      }

      const namedRoute = registrationName(route, index);
      if (this.router.hasRoute(routeNameKey(namedRoute))) {
        return;
      }

      const routeConfig = this.transformer.transform(namedRoute);
      removeRouteFns.push(
        this.router.addRoute(ROOT_LAYOUT_ROUTE_NAME, routeConfig as RouteRecordRaw)
      );
    });

    this.removeRouteFns = removeRouteFns;
    this.registered = true;
  }

  unregister(): void {
    this.removeRouteFns.forEach((fn) => fn());
    this.removeRouteFns = [];
    this.registered = false;
  }

  isRegistered(): boolean {
    return this.registered;
  }

  getRemoveRouteFns(): (() => void)[] {
    return this.removeRouteFns;
  }

  markAsRegistered(): void {
    this.registered = true;
  }
}
