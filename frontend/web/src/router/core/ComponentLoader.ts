/**
 * 组件加载器 —— 菜单字符串路径 → views 懒加载。
 * 支持 module_system→module_platform 回退、param→params 重命名回退。
 */
import { defineComponent, h, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import {
  IframeRouteManager,
  NestedRouterParent,
  ROUTE_COMPONENT_LAYOUT,
  ROUTE_COMPONENT_NESTED_PARENT,
} from "../staticRoutes";

export class ComponentLoader {
  private modules: Record<string, () => Promise<any>>;

  constructor() {
    this.modules = import.meta.glob("../../views/**/*.vue");
  }

  load(componentPath: string): () => Promise<any> {
    if (!componentPath) {
      return this.createEmptyComponent();
    }
    if (componentPath === ROUTE_COMPONENT_LAYOUT || componentPath === "/layouts/index") {
      return this.loadLayout();
    }
    if (componentPath === ROUTE_COMPONENT_NESTED_PARENT) {
      return this.loadNestedParent();
    }

    const normalized = componentPath.startsWith("/")
      ? componentPath
      : `/${componentPath.replace(/^\/+/, "")}`;
    const fullPath = `../../views${normalized}.vue`;
    const fullPathWithIndex = `../../views${normalized}/index.vue`;
    let module = this.modules[fullPath] || this.modules[fullPathWithIndex];

    // Fallback: component moved from module_system to module_platform
    if (!module && normalized.includes("/module_system/")) {
      const altPath = normalized.replace("/module_system/", "/module_platform/");
      module =
        this.modules[`../../views${altPath}.vue`] ||
        this.modules[`../../views${altPath}/index.vue`];
    }
    // Fallback: renamed view directories (param→params)
    if (!module) {
      const renames: Record<string, string> = { "/param/": "/params/" };
      for (const [oldP, newP] of Object.entries(renames)) {
        const alt = normalized.replace(oldP, newP);
        if (alt !== normalized) {
          const fallbackModule =
            this.modules[`../../views${alt}.vue`] || this.modules[`../../views${alt}/index.vue`];
          if (fallbackModule) {
            module = fallbackModule;
            break;
          }
        }
      }
    }

    if (!module) {
      console.error(
        `[ComponentLoader] 未找到组件: ${componentPath}，尝试过的路径: ${fullPath} 和 ${fullPathWithIndex}`
      );
      return this.createErrorComponent(componentPath);
    }
    return module;
  }

  loadLayout(): () => Promise<any> {
    return () => import("@/components/layouts/index.vue");
  }

  loadIframe(): () => Promise<any> {
    return () =>
      Promise.resolve(
        defineComponent({
          name: "IframeView",
          setup() {
            const route = useRoute();
            const isLoading = ref(true);
            const iframeUrl = ref("");

            onMounted(() => {
              const iframeRoute = IframeRouteManager.getInstance().findByPath(route.path);
              if (iframeRoute?.meta) {
                iframeUrl.value = iframeRoute.meta.link || "";
              }
            });

            const handleIframeLoad = () => {
              isLoading.value = false;
            };

            return () =>
              h("div", { class: "box-border w-full h-full", "v-loading": isLoading.value }, [
                h("iframe", {
                  src: iframeUrl.value,
                  frameborder: "0",
                  class: "w-full h-full min-h-[calc(100vh-120px)] border-none",
                  onLoad: handleIframeLoad,
                }),
              ]);
          },
        })
      );
  }

  loadNestedParent(): () => Promise<any> {
    return () => Promise.resolve(NestedRouterParent);
  }

  private createEmptyComponent(): () => Promise<any> {
    return () =>
      Promise.resolve({
        render() {
          return h("div", {});
        },
      });
  }

  private createErrorComponent(componentPath: string): () => Promise<any> {
    return () =>
      Promise.resolve({
        render() {
          return h("div", { class: "route-error" }, `组件未找到: ${componentPath}`);
        },
      });
  }
}
