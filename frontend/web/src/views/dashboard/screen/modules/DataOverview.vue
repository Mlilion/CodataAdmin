<template>
  <div class="panel p1 col-3">
    <div class="panel-hd"><span class="dot accent" />数据概览</div>
    <div class="stats-row">
      <div v-for="c in overview" :key="c.label" class="stat-card" :style="{ borderColor: c.color }">
        <div class="stat-icon" :style="{ background: c.color + '18', color: c.color }">
          {{ c.icon }}
        </div>
        <div>
          <span class="stat-val" :style="{ color: c.color }">{{ c.value }}</span>
          <span class="stat-label">{{ c.label }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";

defineOptions({ name: "DataOverview" });

const overview = ref([
  { label: "总用户数", value: "12,542", icon: "👤", color: "#00d4ff" },
  { label: "今日活跃", value: "387", icon: "⚡", color: "#10b981" },
  { label: "API 调用", value: "89.2K", icon: "🔗", color: "#7c3aed" },
  { label: "告警数", value: "2", icon: "⚠️", color: "#ef4444" },
]);
</script>

<style scoped>
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.stat-card {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 12px;
  background: rgb(12 18 48 / 60%);
  border: 1px solid;
  border-radius: 8px;
  transition:
    transform 0.2s,
    box-shadow 0.3s;
}

.stat-card:hover {
  transform: translateY(-1px);
}

.stat-icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  font-size: 18px;
  border-radius: 8px;
}

.stat-val {
  display: block;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
}

.stat-label {
  font-size: 12px;
  opacity: 0.5;
}
</style>
