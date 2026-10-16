<script setup lang="ts">
import { ref, watch } from "vue";

/**
 * 别名标签输入:值形态与后端约定一致(JSON 数组字符串,如 '["GMV","成交额"]')。
 * 空 → emit undefined(键从 payload 消失),与 DimensionMultiSelect 的序列化语义一致。
 * 脏值(非法 JSON)回显按空处理但不主动 emit,避免打开详情即改写存量数据。
 */
const props = defineProps<{ modelValue?: string; placeholder?: string }>();
const emit = defineEmits<{ (e: "update:modelValue", v: string | undefined): void }>();

const selected = ref<string[]>([]);

function parse(json?: string): string[] {
  if (!json) return [];
  try {
    const a = JSON.parse(json);
    return Array.isArray(a) ? a.map(String).filter((s) => s.trim() !== "") : [];
  } catch {
    return [];
  }
}
function serialize(list: string[]): string | undefined {
  return list.length ? JSON.stringify(list) : undefined;
}
function onChange(v: string[]) {
  selected.value = v ?? [];
  emit("update:modelValue", serialize(selected.value));
}

watch(
  () => props.modelValue,
  (v) => {
    selected.value = parse(v);
  },
  { immediate: true }
);

defineExpose({ selected, parse, serialize, onChange });
</script>

<template>
  <ElSelect
    :model-value="selected"
    multiple
    filterable
    allow-create
    default-first-option
    :reserve-keyword="false"
    collapse-tags
    collapse-tags-tooltip
    clearable
    :placeholder="placeholder ?? '输入别名后回车'"
    @update:model-value="onChange"
  />
</template>
