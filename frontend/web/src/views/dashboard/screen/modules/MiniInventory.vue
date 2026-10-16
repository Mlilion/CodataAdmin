<template>
  <div class="mini-chart-panel">
    <div class="mc-hd">库存周转天数</div>
    <div class="mc-value" style="color: #14b8a6">{{ val }}<span class="mc-unit">天</span></div>
    <div class="progress-wrap">
      <div class="progress-track">
        <div
          class="progress-fill"
          :style="{ width: pct + '%', background: 'linear-gradient(90deg, #14b8a6, #06b6d4)' }"
        />
      </div>
      <div class="progress-markers">
        <span>0</span><span>5</span><span>10</span><span>15</span><span>20</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";

defineOptions({ name: "MiniInventory" });

const val = ref(8.5);
const pct = ref(42.5);
let timer = 0;

function tick() {
  const n = +(6 + Math.random() * 6).toFixed(1);
  val.value = n;
  pct.value = (n / 20) * 100;
}

onMounted(() => {
  tick();
  timer = window.setInterval(tick, 4000);
});
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.mini-chart-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 10px 12px;
  overflow: hidden;
  background: linear-gradient(180deg, rgb(0 30 80 / 55%) 0%, rgb(6 11 36 / 70%) 100%);
  border: 1px solid var(--border, rgb(0 180 255 / 12%));
  border-radius: 10px;
}

.mini-chart-panel::after {
  position: absolute;
  top: 4px;
  left: 4px;
  width: 6px;
  height: 6px;
  content: "";
  border-top: 1px solid rgb(20 184 166 / 40%);
  border-left: 1px solid rgb(20 184 166 / 40%);
}

.mc-hd {
  margin-bottom: 2px;
  font-size: 10px;
  opacity: 0.35;
}

.mc-value {
  margin-bottom: 8px;
  font-size: 16px;
  font-weight: 700;
  line-height: 1;
}

.mc-unit {
  margin-left: 2px;
  font-size: 9px;
  font-weight: 400;
  opacity: 0.4;
}

.progress-wrap {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: flex-end;
}

.progress-track {
  position: relative;
  height: 6px;
  overflow: hidden;
  background: rgb(26 40 80 / 40%);
  border-radius: 3px;
}

.progress-fill {
  position: relative;
  height: 100%;
  border-radius: 3px;
  transition: width 0.8s;
}

.progress-fill::after {
  position: absolute;
  top: -2px;
  right: 0;
  width: 10px;
  height: 10px;
  content: "";
  background: #14b8a6;
  border-radius: 50%;
  box-shadow: 0 0 8px #14b8a6;
}

.progress-markers {
  display: flex;
  justify-content: space-between;
  margin-top: 3px;
  font-size: 8px;
  opacity: 0.25;
}
</style>
