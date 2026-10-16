<script setup lang="ts">
import { computed, ref } from "vue";
import EntityAPI, { type EntityTable } from "@/api/module_semantic/entity";
import { fetchAllPages } from "./listAll";

const props = defineProps<{ modelValue?: number; excludeIds?: number[] }>();
const emit = defineEmits<{
  (e: "update:modelValue", v: number | undefined): void;
  (e: "update:code", v: string | undefined): void;
}>();

const options = ref<EntityTable[]>([]);

/** 供「已入模实体不再可选」等场景过滤;不传 excludeIds 时行为与原来一致 */
const filteredOptions = computed(() => {
  const ex = props.excludeIds;
  if (!ex?.length) return options.value;
  return options.value.filter((e) => !ex.includes(e.id!));
});

async function load() {
  try {
    options.value = await fetchAllPages((q) => EntityAPI.listEntity(q));
  } catch (e) {
    console.warn("[EntitySelect] 加载实体失败,降级为空", e);
    options.value = [];
  }
}
function onChange(id: number) {
  const e = options.value.find((x) => x.id === id);
  emit("update:modelValue", id);
  emit("update:code", e?.code);
}

load();
defineExpose({ onChange, load, options, filteredOptions });
</script>

<template>
  <ElSelect :model-value="props.modelValue" placeholder="选择实体" clearable filterable @update:model-value="onChange">
    <ElOption v-for="e in filteredOptions" :key="e.id" :label="`${e.name} (${e.code})`" :value="e.id!" />
  </ElSelect>
</template>
