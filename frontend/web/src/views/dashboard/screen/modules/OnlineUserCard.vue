<template>
  <div class="panel p1 col-2">
    <div class="panel-hd"><span class="dot accent" />在线用户</div>
    <div class="online-users">
      <div class="online-count">{{ onlineUsers }}</div>
      <div class="online-label">当前在线</div>
      <div class="online-trend">
        <span :class="onlineTrend >= 0 ? 'positive' : 'negative'"
          >{{ onlineTrend >= 0 ? "+" : "" }}{{ onlineTrend }}%</span
        >
        <span class="trend-label">较昨日</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";

defineOptions({ name: "OnlineUserCard" });

const onlineUsers = ref(387);
const onlineTrend = ref(15.6);
</script>

<style scoped>
.online-users {
  padding: 20px 0;
  text-align: center;
}

.online-count {
  font-size: 48px;
  font-weight: 700;
  color: #00d4ff;
}

.online-label {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  opacity: 0.5;
}

.online-trend {
  margin-top: 10px;
}

.online-trend span:first-child {
  font-size: 16px;
  font-weight: 600;
}

.online-trend .positive {
  color: #10b981;
}

.online-trend .negative {
  color: #ef4444;
}

.trend-label {
  margin-left: 4px;
  font-size: 12px;
  opacity: 0.5;
}
</style>
