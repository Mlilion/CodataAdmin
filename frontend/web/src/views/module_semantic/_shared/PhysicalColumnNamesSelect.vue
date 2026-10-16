<script setup lang="ts">
import { computed } from "vue";
import type { CatColumn } from "@/api/module_semantic/catalog";

/**
 * 从物理列清单中选择列名(值为列名字符串)。
 * - multiple: modelValue 为逗号连接的列名(如 "order_id" 或 "db,table"),供实体 grain 使用;
 * - 单选: modelValue 为单个列名,供默认时间字段使用;
 * - preferTime: 日期/时间类型列排前(不隐藏其他列);
 * - 回显保值: modelValue 里已存在但不在 columns 中的名字追加为额外选项,
 *   避免物理表换绑后清掉旧 grain/时间字段数据。
 */
const props = defineProps<{
  modelValue?: string;
  columns: CatColumn[];
  multiple?: boolean;
  preferTime?: boolean;
  placeholder?: string;
  disabled?: boolean;
}>();
const emit = defineEmits<{ (e: "update:modelValue", v: string | undefined): void }>();

const isTime = (c: CatColumn) => /date|timestamp|datetime/i.test(c.col_type ?? "");

const sortedColumns = computed(() => {
  if (!props.preferTime) return props.columns;
  return [...props.columns].sort((a, b) => Number(isTime(b)) - Number(isTime(a)));
});

const selectedNames = computed(() =>
  (props.modelValue ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
);

/** 已保存但当前列清单里不存在的名字,追加为可回显的额外选项 */
const extraNames = computed(() => {
  const known = new Set(props.columns.map((c) => c.name));
  return selectedNames.value.filter((n) => !known.has(n));
});

function onChange(v: string | string[] | undefined) {
  const list = Array.isArray(v) ? v : v ? [v] : [];
  if (props.multiple) {
    emit("update:modelValue", list.length ? list.join(",") : undefined);
  } else {
    emit("update:modelValue", list[0] || undefined);
  }
}

defineExpose({ selectedNames, extraNames, sortedColumns, onChange });
</script>

<template>
  <ElSelect
    :model-value="multiple ? selectedNames : modelValue"
    :multiple="multiple"
    collapse-tags
    collapse-tags-tooltip
    filterable
    clearable
    :disabled="disabled"
    :placeholder="placeholder ?? (disabled ? '请先选择物理表' : '选择物理列')"
    @update:model-value="onChange"
  >
    <ElOption
      v-for="c in sortedColumns"
      :key="c.name"
      :label="c.comment ? `${c.name} (${c.comment})` : c.name"
      :value="c.name"
    />
    <ElOption v-for="n in extraNames" :key="`extra-${n}`" :label="n" :value="n" />
  </ElSelect>
</template>
