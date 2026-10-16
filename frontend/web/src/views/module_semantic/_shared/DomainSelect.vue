<script setup lang="ts">
import { ref } from "vue";
import DomainAPI, { type DomainTable } from "@/api/module_semantic/domain";
import { fetchAllPages } from "./listAll";

const props = defineProps<{ modelValue?: string }>();
const emit = defineEmits<{ (e: "update:modelValue", v: string | undefined): void }>();

const options = ref<DomainTable[]>([]);

async function load() {
  try {
    options.value = await fetchAllPages((q) => DomainAPI.listDomain(q));
  } catch (e) {
    console.warn("[DomainSelect] 加载业务域失败,降级为空", e);
    options.value = [];
  }
}
function onChange(code: string | undefined) {
  emit("update:modelValue", code);
}

load();
defineExpose({ load, onChange, options });
</script>

<template>
  <ElSelect
    :model-value="props.modelValue"
    placeholder="选择业务域"
    clearable
    filterable
    @update:model-value="onChange"
  >
    <ElOption v-for="d in options" :key="d.id" :label="`${d.name} (${d.code})`" :value="d.code" />
  </ElSelect>
</template>
