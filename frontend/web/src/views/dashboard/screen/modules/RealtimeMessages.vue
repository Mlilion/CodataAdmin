<template>
  <div class="panel p1">
    <div class="panel-hd">
      <span class="dot accent" />实时操作日志
      <span class="msg-count">NEW</span>
    </div>
    <div class="msg-scroll">
      <div class="msg-inner">
        <div v-for="(m, i) in messagesDuplicated" :key="i" class="msg-row">
          <span class="msg-time">{{ m.time }}</span>
          <span class="msg-tag" :class="m.tag">{{ m.tagText }}</span>
          <span>{{ m.text }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: "RealtimeMessages" });

const messages = [
  {
    time: "22:30",
    tag: "order",
    tagText: "交易",
    text: "商户「星辰科技」完成大额订单支付 ¥86,400",
  },
  { time: "22:15", tag: "system", tagText: "系统", text: "数据备份定时任务执行完成，耗时 12.3s" },
  { time: "21:52", tag: "audit", tagText: "审计", text: "运营主管 zhangsan 导出本月交易报表" },
  { time: "21:30", tag: "deploy", tagText: "部署", text: "v3.2.1 版本灰度发布至华东区节点" },
  { time: "21:05", tag: "notice", tagText: "公告", text: "系统公告: 6月1日进行数据库扩容维护" },
  {
    time: "20:40",
    tag: "alarm",
    tagText: "告警",
    text: "[已恢复] 华南区 API 网关延迟突增至 320ms",
  },
  { time: "20:15", tag: "order", tagText: "交易", text: "退款工单 #TK-2024-08921 已自动处理完成" },
  {
    time: "19:52",
    tag: "system",
    tagText: "系统",
    text: "缓存集群节点 node-07 内存使用率降至 62%",
  },
];

const messagesDuplicated = [...messages, ...messages];
</script>

<style scoped>
.msg-scroll {
  position: relative;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.msg-inner {
  width: 100%;
  animation: scrollUp 28s linear infinite;
}

.msg-row {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 5px 0;
  font-size: 11px;
  border-bottom: 1px solid rgb(26 40 80 / 30%);
  opacity: 0.75;
}

.msg-time {
  flex-shrink: 0;
  width: 38px;
  font-variant-numeric: tabular-nums;
  opacity: 0.4;
}

.msg-tag {
  flex-shrink: 0;
  padding: 1px 4px;
  font-size: 9px;
  border-radius: 3px;
}

.msg-tag.order {
  color: #00d4ff;
  background: rgb(0 212 255 / 15%);
}

.msg-tag.system {
  color: #7c3aed;
  background: rgb(124 58 237 / 15%);
}

.msg-tag.audit {
  color: #10b981;
  background: rgb(16 185 129 / 15%);
}

.msg-tag.deploy {
  color: #f59e0b;
  background: rgb(245 158 11 / 15%);
}

.msg-tag.notice {
  color: #00d4ff;
  background: rgb(0 212 255 / 15%);
}

.msg-tag.alarm {
  color: #ef4444;
  background: rgb(239 68 68 / 15%);
}

.msg-count {
  padding: 2px 6px;
  margin-left: auto;
  font-size: 10px;
  color: #00d4ff;
  background: rgb(0 212 255 / 15%);
  border-radius: 10px;
  opacity: 0.6;
}
@keyframes scrollUp {
  0% {
    transform: translateY(0);
  }

  100% {
    transform: translateY(-50%);
  }
}
</style>
