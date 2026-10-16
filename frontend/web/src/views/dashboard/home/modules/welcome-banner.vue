<!-- 首页欢迎横幅：个性化问候 + 日期 + 系统名 -->
<template>
  <div class="welcome-banner fa-card relative overflow-hidden">
    <div class="welcome-banner__content relative z-1 flex flex-col gap-2">
      <p class="welcome-banner__date">{{ todayText }}</p>
      <h2 class="welcome-banner__title">
        {{ greetingPrefix }}，<span class="welcome-banner__name">{{ userName }}</span>
      </h2>
      <p class="welcome-banner__subtitle">
        欢迎回到 {{ systemName }}，祝你今天工作顺利。
      </p>
    </div>
    <img class="welcome-banner__decoration" :src="decorationIcon" alt="" aria-hidden="true" />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import decorationIcon from "@imgs/3d/icon1.webp";
import { useUserStore } from "@stores";
import AppConfig from "@/config";

defineOptions({ name: "HomeWelcomeBanner" });

const userStore = useUserStore();

const userName = computed(() => userStore.basicInfo.name || userStore.basicInfo.username || "用户");

const systemName = computed(() => AppConfig.systemInfo.name);

const greetingPrefix = computed(() => {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 9) return "早上好";
  if (h < 12) return "上午好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
});

const todayText = computed(() => {
  const d = new Date();
  const week = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][d.getDay()];
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 · ${week}`;
});
</script>

<style scoped lang="scss">
.welcome-banner {
  display: flex;
  align-items: center;
  min-height: 8.5rem;
  padding: 1.75rem 2rem;
  background: linear-gradient(
    120deg,
    color-mix(in srgb, var(--el-color-primary) 10%, var(--el-bg-color)) 0%,
    var(--el-bg-color) 60%
  );
}

.welcome-banner__date {
  margin: 0;
  font-size: 0.8rem;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.02em;
}

.welcome-banner__title {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
  line-height: 1.3;
  color: var(--el-text-color-primary);
}

.welcome-banner__name {
  color: var(--el-color-primary);
}

.welcome-banner__subtitle {
  margin: 0;
  font-size: 0.9rem;
  color: var(--el-text-color-regular);
}

.welcome-banner__decoration {
  position: absolute;
  right: 1.75rem;
  bottom: -0.5rem;
  width: 8.5rem;
  pointer-events: none;
  opacity: 0.92;
  user-select: none;
}

@media (width <= 640px) {
  .welcome-banner__decoration {
    display: none;
  }
}
</style>
