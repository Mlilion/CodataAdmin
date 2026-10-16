<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import CatalogAPI, { type CatColumn } from "@/api/module_semantic/catalog";

const props = defineProps<{ modelValue?: number; tableId?: number; columns?: CatColumn[] }>();
const emit = defineEmits<{
  (e: "update:modelValue", v: number | undefined): void;
  (e: "update:meta", v: { column?: string; colType?: string; comment?: string }): void;
}>();

const loadedColumns = ref<CatColumn[]>([]);
const columns = computed(() => (props.columns?.length ? props.columns : loadedColumns.value));
const loading = ref(false);
let loadRequestId = 0;

async function loadColumns(tableId?: number) {
  const requestId = ++loadRequestId;
  loadedColumns.value = [];
  if (props.columns?.length || !tableId) {
    loading.value = false;
    return;
  }
  loading.value = true;
  try {
    const res = await CatalogAPI.tableDetail(tableId);
    if (requestId !== loadRequestId || props.tableId !== tableId || props.columns?.length) {
      return;
    }
    loadedColumns.value = res.data.data?.columns ?? [];
  } catch (e) {
    if (requestId === loadRequestId && props.tableId === tableId && !props.columns?.length) {
      console.warn("[PhysicalColumnSelect] 加载物理列失败,降级为空", e);
      loadedColumns.value = [];
    }
  } finally {
    if (requestId === loadRequestId) loading.value = false;
  }
}
function onColumnChange(id?: number) {
  const c = columns.value.find((x) => x.id === id);
  emit("update:modelValue", id);
  // 命中列时附带类型/注释,供父表单回填(清空仍为 {},保持既有契约)
  emit("update:meta", c ? { column: c.name, colType: c.col_type, comment: c.comment } : {});
}
let mounted = false;
watch(
  () => props.tableId,
  (t, prev) => {
    if (mounted && t !== prev) {
      emit("update:modelValue", undefined);
      emit("update:meta", {});
    }
    void loadColumns(t);
  },
  { immediate: true }
);
onMounted(() => {
  mounted = true;
});
defineExpose({ onColumnChange, loadColumns });
</script>

<template>
  <ElSelect
    :model-value="props.modelValue"
    placeholder="物理列"
    clearable
    filterable
    :loading="loading"
    :disabled="!props.tableId"
    @update:model-value="onColumnChange"
  >
    <ElOption v-for="c in columns" :key="c.id" :label="c.name" :value="c.id" />
  </ElSelect>
</template>
