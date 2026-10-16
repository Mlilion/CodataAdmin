<template>
  <div class="panel p1 col-3">
    <div class="panel-hd"><span class="dot green" />服务器状态</div>
    <div class="server-status">
      <div v-for="server in servers" :key="server.name" class="server-item">
        <div class="server-header">
          <span class="server-name">{{ server.name }}</span>
          <span class="server-status-dot" :class="server.status" />
        </div>
        <div class="server-metrics">
          <span>CPU: {{ server.cpu }}%</span>
          <span>内存: {{ server.memory }}%</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";

defineOptions({ name: "ServerStatusList" });

const servers = ref([
  { name: "API Server-01", status: "online", cpu: 45, memory: 62 },
  { name: "API Server-02", status: "online", cpu: 38, memory: 58 },
  { name: "DB Server-01", status: "online", cpu: 25, memory: 72 },
  { name: "Redis Server", status: "online", cpu: 15, memory: 45 },
]);
</script>

<style scoped>
.server-status {
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: auto;
}

.server-item {
  padding: 10px;
  background: rgb(12 18 48 / 60%);
  border-radius: 8px;
}

.server-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.server-name {
  font-size: 12px;
}

.server-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.server-status-dot.online {
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
}

.server-status-dot.offline {
  background: #ef4444;
}

.server-metrics {
  display: flex;
  gap: 12px;
  font-size: 11px;
  opacity: 0.6;
}
</style>
