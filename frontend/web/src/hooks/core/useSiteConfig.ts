/**
 * useSiteConfig - 站点配置初始化（标题 + favicon）。
 *
 * 从 configStore 拉取系统配置，同步到浏览器标题和 favicon。
 * 通过 watch 响应配置变更（如管理员在后台修改后重新拉取时自动更新）。
 *
 * 应在 App.vue 的 onMounted 中调用。
 */

import { watch } from "vue";
import { useConfigStore } from "@stores";

const updateFavicon = (url: string) => {
  const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (link) link.href = url;
};

const syncFromConfig = () => {
  const { name, favicon } = useConfigStore().configData;
  if (name?.config_value) document.title = name.config_value;
  if (favicon?.config_value) updateFavicon(favicon.config_value);
};

export function useSiteConfig() {
  const configStore = useConfigStore();

  /** 初始化：强制拉取配置并同步标题/favicon */
  const initSiteConfig = async () => {
    try {
      await configStore.getConfig(true);
      syncFromConfig();
    } catch (error) {
      console.error("[SiteConfig] 获取配置失败:", error);
    }
  };

  /** 配置更新后自动同步（管理员后台修改配置后重新拉取时） */
  watch(
    () => configStore.configData,
    () => syncFromConfig(),
    { deep: false }
  );

  return { initSiteConfig };
}
