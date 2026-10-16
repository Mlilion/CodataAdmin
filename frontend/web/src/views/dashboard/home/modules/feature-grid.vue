<!-- 首页常用功能入口：取真实顶层菜单模块生成网格卡片 -->
<template>
  <div class="feature-grid fa-card p-5">
    <div class="fa-card-header mb-4">
      <div class="title">
        <h4>常用功能</h4>
        <p class="subtitle">快速进入各业务模块</p>
      </div>
    </div>

    <div v-if="entries.length" class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <button
        v-for="entry in entries"
        :key="entry.path"
        type="button"
        class="feature-item"
        @click="go(entry.path)"
      >
        <span class="feature-item__icon">
          <FaMenuRouteIcon :icon="entry.icon || 'menu'" :size="22" />
        </span>
        <span class="feature-item__label">{{ entry.title }}</span>
      </button>
    </div>

    <ElEmpty v-else description="暂无可用功能" :image-size="60" />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useMenuStore } from "@stores";
import { formatMenuTitle } from "@utils";
import type { AppRouteRecord } from "@/types/router";

defineOptions({ name: "HomeFeatureGrid" });

const router = useRouter();
const menuStore = useMenuStore();

interface Entry {
  title: string;
  icon?: string;
  path: string;
}

/** 深度优先找第一个可跳转的叶子路径 */
function firstLeafPath(item: AppRouteRecord): string | null {
  if (item.meta?.isHide) return null;
  if (item.children && item.children.length > 0) {
    for (const child of item.children) {
      const p = firstLeafPath(child);
      if (p) return p;
    }
    return null;
  }
  if (item.path && item.path.trim()) return item.path;
  return null;
}

/** 顶层可见菜单 → 入口卡片 */
const entries = computed<Entry[]>(() => {
  const list: Entry[] = [];
  for (const m of menuStore.menuList) {
    if (m.meta?.isHide) continue;
    const path = firstLeafPath(m);
    if (!path) continue;
    list.push({
      title: formatMenuTitle(m.meta?.title as string),
      icon: m.meta?.icon,
      path,
    });
  }
  return list;
});

function go(path: string) {
  router.push(path);
}
</script>

<style scoped lang="scss">
.fa-card-header .title h4 {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.fa-card-header .title .subtitle {
  margin: 0.15rem 0 0;
  font-size: 0.78rem;
  color: var(--el-text-color-secondary);
}

.feature-item {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  align-items: center;
  justify-content: center;
  padding: 1.1rem 0.75rem;
  cursor: pointer;
  background: var(--el-fill-color-lighter);
  border: 1px solid transparent;
  border-radius: 12px;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    transform 0.2s ease;

  &:hover {
    background: var(--el-bg-color);
    border-color: color-mix(in srgb, var(--el-color-primary) 35%, transparent);
    transform: translateY(-2px);
  }
}

.feature-item__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.75rem;
  height: 2.75rem;
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 12%, transparent);
  border-radius: 10px;
}

.feature-item__label {
  font-size: 0.82rem;
  font-weight: 500;
  line-height: 1.2;
  color: var(--el-text-color-regular);
  text-align: center;
}
</style>
