<script setup lang="ts">
import { ref, watch } from "vue";
import DimensionAPI, { type DimensionTable } from "@/api/module_semantic/dimension";
import { fetchAllPages } from "./listAll";

const props = defineProps<{ modelValue?: string }>();
const emit = defineEmits<{ (e: "update:modelValue", v: string | undefined): void }>();

const options = ref<DimensionTable[]>([]);
const selected = ref<number[]>([]);
const loading = ref(false);

function parse(json?: string): number[] {
  if (!json) return [];
  try {
    const a = JSON.parse(json);
    return Array.isArray(a) ? a.map(Number).filter((n) => !Number.isNaN(n)) : [];
  } catch {
    return [];
  }
}
function serialize(ids: number[]): string | undefined {
  return ids.length ? JSON.stringify(ids) : undefined;
}

async function load() {
  loading.value = true;
  try {
    options.value = await fetchAllPages((q) => DimensionAPI.listDimension(q));
  } catch (e) {
    console.warn("[DimensionMultiSelect] 加载维度失败,降级为空", e);
    options.value = [];
  } finally {
    loading.value = false;
  }
}
function onChange(ids: number[]) {
  selected.value = ids;
  emit("update:modelValue", serialize(ids));
}

watch(
  () => props.modelValue,
  (v) => {
    selected.value = parse(v);
  },
  { immediate: true },
);

load();
defineExpose({ selected, options, loading, onChange, parse, serialize, load });
</script>

<template>
  <ElSelect
    :model-value="selected"
    multiple
    collapse-tags
    collapse-tags-tooltip
    placeholder="选择可用维度"
    clearable
    filterable
    :loading="loading"
    @update:model-value="onChange"
  >
    <ElOption v-for="d in options" :key="d.id" :label="`${d.name} (${d.code})`" :value="d.id!" />
  </ElSelect>
</template>
