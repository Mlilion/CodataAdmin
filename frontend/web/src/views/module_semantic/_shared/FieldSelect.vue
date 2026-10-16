<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import EntityAPI, { type SemField } from "@/api/module_semantic/entity";

const props = defineProps<{ modelValue?: string; entityId?: number }>();
const emit = defineEmits<{ (e: "update:modelValue", v: string | undefined): void }>();

const fields = ref<SemField[]>([]);

async function load(id?: number) {
  fields.value = [];
  if (!id) return;
  try {
    const r = await EntityAPI.detailEntity(id);
    fields.value = r.data.data?.fields ?? [];
  } catch (e) {
    console.warn("[FieldSelect] 加载字段失败,降级为空", e);
    fields.value = [];
  }
}
function onChange(code: string | undefined) {
  emit("update:modelValue", code);
}

let mounted = false;
watch(
  () => props.entityId,
  (id, prev) => {
    if (mounted && id !== prev) emit("update:modelValue", undefined);
    load(id);
  },
  { immediate: true }
);
onMounted(() => { mounted = true; });

defineExpose({ load, onChange, fields });
</script>

<template>
  <ElSelect
    :model-value="props.modelValue"
    placeholder="选择字段"
    clearable
    filterable
    :disabled="!props.entityId"
    @update:model-value="onChange"
  >
    <ElOption v-for="f in fields" :key="f.code" :label="`${f.name} (${f.code})`" :value="f.code" />
  </ElSelect>
</template>
