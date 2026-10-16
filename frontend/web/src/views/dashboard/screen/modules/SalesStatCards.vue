<template>
  <div class="panel p1 col-3">
    <div class="panel-hd"><span class="dot green" />销售统计</div>
    <div class="sales-cards">
      <div class="sales-card">
        <span class="sales-label">今日销售额</span>
        <span class="sales-value">¥{{ formatNumber(todaySales) }}</span>
        <span class="sales-change positive">+12.5%</span>
      </div>
      <div class="sales-card">
        <span class="sales-label">订单数量</span>
        <span class="sales-value">{{ todayOrders }}</span>
        <span class="sales-change positive">+8.3%</span>
      </div>
      <div class="sales-card">
        <span class="sales-label">转化率</span>
        <span class="sales-value">{{ conversionRate }}%</span>
        <span class="sales-change negative">-2.1%</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";

defineOptions({ name: "SalesStatCards" });

const todaySales = ref(125860);
const todayOrders = ref(328);
const conversionRate = ref(3.2);

function formatNumber(num: number): string {
  return num.toLocaleString();
}
</script>

<style scoped>
.sales-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sales-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  background: rgb(12 18 48 / 60%);
  border-radius: 8px;
}

.sales-label {
  font-size: 12px;
  opacity: 0.6;
}

.sales-value {
  font-size: 18px;
  font-weight: 600;
  color: #e0e6ff;
}

.sales-change {
  padding: 2px 6px;
  font-size: 12px;
  border-radius: 4px;
}

.sales-change.positive {
  color: #10b981;
  background: rgb(16 185 129 / 20%);
}

.sales-change.negative {
  color: #ef4444;
  background: rgb(239 68 68 / 20%);
}
</style>
