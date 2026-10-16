/**
 * 路由转换器 —— 菜单树 → vue-router 记录。
 * 处理 iframe、一级叶子路由、普通路由三种形态，支持 shellChild 模式。
 */
import type { RouteRecordRaw } from "vue-router";
import type { AppRouteRecord } from "@/types/router";
import { ComponentLoader } from "./ComponentLoader";
import { IframeRouteManager, ROUTE_COMPONENT_LAYOUT } from "../staticRoutes";

interface ConvertedRoute extends Omit<RouteRecordRaw, "children"> {
  id?: number;
  children?: ConvertedRoute[];
  component?: RouteRecordRaw["component"] | (() => Promise<any>);
}

export class RouteTransformer {
  private componentLoader: ComponentLoader;
  private iframeManager: IframeRouteManager;
  private readonly shellChild: boolean;

  constructor(componentLoader: ComponentLoader, options?: { shellChild?: boolean }) {
    this.componentLoader = componentLoader;
    this.shellChild = options?.shellChild ?? false;
    this.iframeManager = IframeRouteManager.getInstance();
  }

  transform(route: AppRouteRecord, depth = 0, parentAbsPath = ""): ConvertedRoute {
    const absPath = (route.path || "").trim();
    const pathOut = this.routerPath(depth, parentAbsPath, absPath);
    const { component, children, ...routeConfig } = route;

    const converted: ConvertedRoute = { ...routeConfig, path: pathOut, component: undefined };

    if (route.meta.isIframe) {
      this.handleIframeRoute(converted, route, depth);
    } else if (this.isFirstLevelLeaf(route, depth)) {
      this.handleFirstLevelLeaf(converted, route, component as string);
    } else {
      this.handleNormalRoute(converted, component as string, depth);
    }

    converted.path = pathOut;

    if (children?.length) {
      converted.children = children.map((c) => this.transform(c, depth + 1, absPath));
    }

    return converted;
  }

  private routerPath(depth: number, parentAbsPath: string, absPath: string): string {
    const firstSeg = absPath.split("/").filter(Boolean)[0] ?? "";
    if (!this.shellChild) {
      if (depth === 0) {
        return firstSeg ? `/${firstSeg}` : "/";
      }
      return absPath;
    }
    if (depth === 0) {
      return firstSeg;
    }
    if (!parentAbsPath || !absPath) return absPath;
    const p = parentAbsPath.replace(/\/$/, "");
    if (absPath.startsWith(`${p}/`)) return absPath.slice(p.length + 1);
    return absPath.split("/").filter(Boolean).pop() ?? absPath;
  }

  private isFirstLevelLeaf(route: AppRouteRecord, depth: number): boolean {
    return depth === 0 && (!route.children || route.children.length === 0);
  }

  private handleIframeRoute(
    targetRoute: ConvertedRoute,
    sourceRoute: AppRouteRecord,
    depth: number
  ): void {
    if (depth === 0) {
      targetRoute.component = this.shellChild
        ? this.componentLoader.loadNestedParent()
        : this.componentLoader.loadLayout();
      targetRoute.name = "";
      const leafPath = this.shellChild ? "" : sourceRoute.path || "";
      targetRoute.children = [
        {
          ...sourceRoute,
          path: leafPath,
          component: this.componentLoader.loadIframe(),
        } as ConvertedRoute,
      ];
    } else {
      targetRoute.component = this.componentLoader.loadIframe();
    }
    this.iframeManager.add(sourceRoute);
  }

  private handleFirstLevelLeaf(
    converted: ConvertedRoute,
    route: AppRouteRecord,
    component: string | undefined
  ): void {
    converted.component = this.shellChild
      ? this.componentLoader.loadNestedParent()
      : this.componentLoader.loadLayout();
    converted.name = "";
    route.meta.isFirstLevel = true;
    const leafPath = this.shellChild ? "" : route.path || "";
    converted.children = [
      {
        ...route,
        path: leafPath,
        component: component ? this.componentLoader.load(component) : undefined,
      } as ConvertedRoute,
    ];
  }

  private handleNormalRoute(
    converted: ConvertedRoute,
    component: string | undefined,
    depth: number
  ): void {
    if (!component) return;
    if (this.shellChild && depth === 0 && component === ROUTE_COMPONENT_LAYOUT) {
      converted.component = this.componentLoader.loadNestedParent();
      return;
    }
    converted.component = this.componentLoader.load(component);
  }
}
