<!-- 授权页顶栏：左上 Logo / 标题 / 版本（固定），右上操作（固定）；切换布局时仅下方主体变化 -->
<template>
  <header
    class="auth-top-bar pointer-events-none fixed left-0 right-0 top-0 z-100 flex items-center justify-between gap-3 bg-transparent px-5 py-4.5 md:gap-4 md:px-10"
  >
    <div class="pointer-events-auto flex min-w-0 flex-1 items-center gap-3">
      <FaLogo class="icon shrink-0" size="46" :src="webLogoSrc" />
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="auth-top-bar__site-title">{{ siteTitle }}</h1>
          <div class="logo-version-badge shrink-0" :title="displayVersion">
            <span class="logo-version-pill">{{ displayVersion }}</span>
          </div>
        </div>
      </div>
    </div>

    <div
      class="auth-top-bar-actions-panel pointer-events-auto flex shrink-0 items-center justify-center gap-1.5 px-2 py-1.5 max-sm:mr-1"
    >
      <ElDropdown
        v-if="shouldShowLanguage"
        @command="changeLanguage"
        popper-class="langDropDownStyle"
      >
        <div
          class="btn language-btn auth-top-bar__action h-8 w-8 cursor-pointer flex items-center justify-center transition duration-300"
        >
          <FaSvgIcon
            icon="ri:translate-2"
            class="text-[19px] text-g-800 transition-colors duration-300"
          />
        </div>
        <template #dropdown>
          <ElDropdownMenu>
            <div v-for="lang in languageOptions" :key="lang.value" class="lang-btn-item">
              <ElDropdownItem
                :command="lang.value"
                :class="{ 'is-selected': locale === lang.value }"
              >
                <span class="menu-txt">{{ lang.label }}</span>
                <FaSvgIcon icon="ri:check-fill" class="text-base" v-if="locale === lang.value" />
              </ElDropdownItem>
            </div>
          </ElDropdownMenu>
        </template>
      </ElDropdown>
      <div
        v-if="shouldShowThemeToggle"
        class="btn theme-btn auth-top-bar__action h-8 w-8 cursor-pointer flex items-center justify-center transition duration-300"
        @click="themeAnimation"
      >
        <FaSvgIcon
          :icon="isDark ? 'ri:sun-fill' : 'ri:moon-line'"
          class="text-xl text-g-800 transition-colors duration-300"
        />
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { storeToRefs } from "pinia";
import { useI18n } from "vue-i18n";
import { useSettingsStore, useUserStore, useConfigStore } from "@stores";
import { useHeaderBar } from "@/hooks/core/useHeaderBar";
import { themeAnimation } from "@utils";
import { languageOptions } from "@/locales";
import { LanguageEnum } from "@/enums/appEnum";
import AppConfig from "@/config";

defineOptions({ name: "AuthTopBar" });

const DEFAULT_APP_VERSION = "3.0.0";

const configStore = useConfigStore();
const settingStore = useSettingsStore();
const userStore = useUserStore();
const { isDark } = storeToRefs(settingStore);
const { shouldShowThemeToggle, shouldShowLanguage } = useHeaderBar();
const { locale } = useI18n();

const webLogoSrc = computed(
  () => configStore.configData.tenant_logo?.config_value?.trim() || undefined
);

const siteTitle = computed(
  () => configStore.configData.tenant_name?.config_value?.trim() || AppConfig.systemInfo.name
);

const displayVersion = computed(() => {
  const raw = configStore.configData.tenant_version?.config_value?.trim();
  const ver = raw || DEFAULT_APP_VERSION;
  return ver.startsWith("v") || ver.startsWith("V") ? ver : `v${ver}`;
});

const changeLanguage = (lang: LanguageEnum) => {
  if (locale.value === lang) return;
  locale.value = lang;
  userStore.setLanguage(lang);
};
</script>

<style scoped>
.auth-top-bar__site-title {
  max-width: min(52vw, 28rem);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: clamp(1rem, 2.2vw, 1.25rem);
  font-weight: 600;
  line-height: 1.35;
  color: var(--el-text-color-primary);
  letter-spacing: -0.02em;
  white-space: nowrap;
}

.logo-version-pill {
  display: inline-block;
  padding: 0.28rem 0.6rem;
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
  color: var(--el-color-primary);
  letter-spacing: 0.02em;
  background: color-mix(in srgb, var(--el-color-primary) 11%, transparent);
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 28%, transparent);
  border-radius: 999px;
}

/* 右上角操作的整体衬底 */
.auth-top-bar-actions-panel {
  background-color: var(--el-fill-color-blank);
  border: 1px solid var(--el-border-color-lighter);

  /* 胶囊形：左右两端为半圆弧 */
  border-radius: 9999px;
  box-shadow: 0 2px 12px rgb(0 0 0 / 6%);
  backdrop-filter: blur(10px);
}

.dark .auth-top-bar-actions-panel {
  background-color: rgb(255 255 255 / 8%);
  border-color: rgb(255 255 255 / 12%);
  box-shadow: 0 4px 20px rgb(0 0 0 / 35%);
}

/* 右上角三个操作按钮：悬浮抬升 + 浅底 + 图标随主色 */
.auth-top-bar__action {
  border-radius: 10px;
  transition:
    background-color 0.22s ease,
    transform 0.22s ease,
    box-shadow 0.22s ease;
}

.auth-top-bar__action:hover {
  background-color: var(--el-fill-color-light);
  box-shadow: 0 4px 14px rgb(0 0 0 / 8%);
  transform: translateY(-2px);
}

.auth-top-bar__action:hover :deep(.fa-svg-icon) {
  color: var(--el-color-primary);
}

.auth-top-bar__action:active {
  box-shadow: 0 2px 6px rgb(0 0 0 / 6%);
  transform: translateY(0);
  transition-duration: 0.12s;
}

.dark .auth-top-bar__action:hover {
  background-color: rgb(255 255 255 / 10%);
  box-shadow: 0 4px 18px rgb(0 0 0 / 45%);
}
</style>
