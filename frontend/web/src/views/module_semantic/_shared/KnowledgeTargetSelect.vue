<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import EntityAPI from "@/api/module_semantic/entity";
import DimensionAPI from "@/api/module_semantic/dimension";
import IndicatorAPI from "@/api/module_semantic/indicator";
import JoinAPI from "@/api/module_semantic/join";
import KnowledgeAPI from "@/api/module_semantic/knowledge";
import { fetchAllPages } from "./listAll";

const props = defineProps<{ modelValue?: number; targetType?: string }>();
const emit = defineEmits<{
  (e: "update:modelValue", v: number | undefined): void;
  (e: "update:label", v: string | undefined): void;
}>();

const options = ref<{ id: number; label: string }[]>([]);
const loading = ref(false);

async function load(t?: string) {
  options.value = [];
  if (!t || t === "field") return;
  loading.value = true;
  try {
    if (t === "entity") {
      const items = await fetchAllPages((q) => EntityAPI.listEntity(q));
      options.value = items.map((x) => ({ id: x.id!, label: `${x.name} (${x.code})` }));
    } else if (t === "dimension") {
      const items = await fetchAllPages((q) => DimensionAPI.listDimension(q));
      options.value = items.map((x) => ({ id: x.id!, label: `${x.name} (${x.code})` }));
    } else if (t === "indicator") {
      const items = await fetchAllPages((q) => IndicatorAPI.listIndicator(q));
      options.value = items.map((x) => ({ id: x.id!, label: `${x.name} (${x.code})` }));
    } else if (t === "join") {
      const items = await fetchAllPages((q) => JoinAPI.listJoin(q));
      options.value = items.map((x) => ({ id: x.id!, label: x.on_expr ?? String(x.id) }));
    } else if (t === "knowledge") {
      const items = await fetchAllPages((q) => KnowledgeAPI.listKnowledge(q));
      options.value = items.map((x) => ({ id: x.id!, label: x.summary ?? x.key }));
    }
  } catch (e) {
    console.warn("[KnowledgeTargetSelect] 加载目标对象失败,降级为空", e);
    options.value = [];
  } finally {
    loading.value = false;
  }
}
function onChange(id: number) {
  const o = options.value.find((x) => x.id === id);
  emit("update:modelValue", id);
  emit("update:label", o?.label);
}

let mounted = false;
watch(
  () => props.targetType,
  (t, prev) => {
    if (mounted && t !== prev) emit("update:modelValue", undefined);
    load(t);
  },
  { immediate: true },
);
onMounted(() => { mounted = true; });

defineExpose({ options, loading, onChange, load });
</script>

<template>
  <ElSelect
    :model-value="props.modelValue"
    placeholder="选择目标对象"
    clearable
    filterable
    :loading="loading"
    :disabled="!props.targetType || props.targetType === 'field'"
    @update:model-value="onChange"
  >
    <ElOption v-for="o in options" :key="o.id" :label="o.label" :value="o.id" />
  </ElSelect>
</template>
