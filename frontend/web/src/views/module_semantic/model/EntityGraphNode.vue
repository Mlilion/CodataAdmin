<template>
  <article class="entity-node" :class="`entity-node--${entity.completeness?.light ?? 'neutral'}`">
    <Handle
      id="target-left"
      type="target"
      :position="Position.Left"
      class="entity-handle entity-handle--target"
    />
    <Handle
      id="source-left"
      type="source"
      :position="Position.Left"
      class="entity-handle entity-handle--source"
    />
    <Handle
      id="target-right"
      type="target"
      :position="Position.Right"
      class="entity-handle entity-handle--target"
    />
    <Handle
      id="source-right"
      type="source"
      :position="Position.Right"
      class="entity-handle entity-handle--source"
    />

    <header class="entity-node__header">
      <div class="entity-node__title-wrap">
        <span class="asset-light" :class="lightClass(entity.completeness?.light)" />
        <div>
          <div class="entity-node__title">{{ entity.name }}</div>
          <div class="entity-node__code">{{ entity.code }}</div>
        </div>
      </div>
      <span class="entity-node__type">{{ roleText }}</span>
    </header>

    <div v-for="group in groups" :key="group.key" class="entity-group">
      <div class="entity-group__title">
        <span>{{ group.label }}</span>
        <span>{{ group.total }}</span>
      </div>
      <div v-for="item in group.items" :key="`${group.key}-${item.id}`" class="entity-asset-row">
        <FaSvgIcon :icon="group.icon" class="entity-asset-row__icon" />
        <code>{{ item.code }}</code>
        <span class="entity-asset-row__name">{{ item.name }}</span>
        <span class="asset-light" :class="lightClass(item.light)" />
      </div>
      <div v-if="group.total > group.items.length" class="entity-group__more">
        另有 {{ group.total - group.items.length }} 项
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { Handle, Position } from "@vue-flow/core";
import type { TreeEntityNode } from "@/api/module_semantic/model";

const props = defineProps<{ data: { entity: TreeEntityNode } }>();

interface AssetPreview {
  id: number;
  code: string;
  name: string;
  light?: "green" | "yellow" | "red";
}

interface AssetGroup {
  key: string;
  label: string;
  icon: string;
  total: number;
  items: AssetPreview[];
}

const entity = computed(() => props.data.entity);
const roleText = computed(() => {
  const type = entity.value.entity_type ?? "entity";
  return entity.value.role === "primary" ? `主事实 · ${type}` : type;
});

const groups = computed<AssetGroup[]>(() => {
  const value = entity.value;
  const output: AssetGroup[] = [];

  if (value.fields.length) {
    output.push({
      key: "fields",
      label: "字段",
      icon: "ri:price-tag-3-line",
      total: value.fields.length,
      items: value.fields.slice(0, 4).map((item) => ({ ...item, light: item.completeness?.light })),
    });
  }
  if (value.dimensions.length) {
    output.push({
      key: "dimensions",
      label: "维度",
      icon: "ri:node-tree",
      total: value.dimensions.length,
      items: value.dimensions
        .slice(0, 2)
        .map((item) => ({ ...item, light: item.completeness?.light })),
    });
  }
  if (value.indicators.length) {
    output.push({
      key: "indicators",
      label: "指标",
      icon: "ri:bar-chart-box-line",
      total: value.indicators.length,
      items: value.indicators
        .slice(0, 2)
        .map((item) => ({ ...item, light: item.completeness?.light })),
    });
  }
  if (value.knowledge.length) {
    output.push({
      key: "knowledge",
      label: "知识",
      icon: "ri:book-2-line",
      total: value.knowledge.length,
      items: value.knowledge.slice(0, 2).map((item) => ({
        id: item.id,
        code: item.key,
        name: item.summary,
        light: item.completeness?.light,
      })),
    });
  }

  return output;
});

function lightClass(light?: "green" | "yellow" | "red") {
  return `asset-light--${light ?? "neutral"}`;
}
</script>

<style scoped lang="scss">
.entity-node {
  width: 272px;
  overflow: hidden;
  color: var(--el-text-color-primary);
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgb(15 23 42 / 8%);
}

.entity-node--red {
  border-color: var(--el-color-danger-light-5);
}

.entity-node--yellow {
  border-color: var(--el-color-warning-light-5);
}

.entity-node__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 12px 14px 10px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.entity-node__title-wrap {
  display: flex;
  gap: 9px;
  align-items: flex-start;
}

.entity-node__title {
  font-size: 15px;
  font-weight: 650;
  line-height: 1.2;
}

.entity-node__code,
.entity-asset-row code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.entity-node__code {
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.entity-node__type {
  padding: 2px 7px;
  font-size: 10px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-radius: 4px;
}

.entity-group {
  padding: 8px 12px 9px;
  border-bottom: 1px solid var(--el-border-color-lighter);

  &:last-child {
    border-bottom: 0;
  }
}

.entity-group__title {
  display: flex;
  justify-content: space-between;
  margin-bottom: 5px;
  font-size: 10px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  letter-spacing: 0.04em;
}

.entity-asset-row {
  display: grid;
  grid-template-columns: 14px minmax(72px, 1fr) minmax(58px, 0.8fr) 8px;
  gap: 5px;
  align-items: center;
  min-height: 24px;
  font-size: 10px;
}

.entity-asset-row__icon {
  color: var(--el-text-color-placeholder);
}

.entity-asset-row code,
.entity-asset-row__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entity-asset-row__name {
  color: var(--el-text-color-secondary);
  text-align: right;
}

.entity-group__more {
  padding-top: 3px;
  font-size: 10px;
  color: var(--el-color-primary);
  text-align: right;
}

.asset-light {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-top: 4px;
  background: var(--el-border-color);
  border-radius: 50%;
}

.asset-light--green {
  background: var(--el-color-success);
}

.asset-light--yellow {
  background: var(--el-color-warning);
}

.asset-light--red {
  background: var(--el-color-danger);
}

.asset-light--neutral {
  background: var(--el-text-color-placeholder);
}

.entity-handle {
  width: 8px;
  height: 8px;
  background: #64748b;
  border: 2px solid #fff;
}

.entity-handle--target {
  top: 44%;
}

.entity-handle--source {
  top: 56%;
}
</style>
