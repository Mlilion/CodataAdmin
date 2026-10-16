<script setup lang="ts">
import { ref } from "vue";
import { ElMessage } from "element-plus";
import type { CatColumn } from "@/api/module_semantic/catalog";
import { insertAtCursor, resolveTextarea } from "./sqlInsert";

/**
 * 「插入列」辅助下拉:选中物理列名后插入 getTarget() 解析出的 textarea 光标处,
 * 通过 insert 事件把拼接后的完整文本交回父级(由父级写回 v-model 字段)。
 */
const props = defineProps<{
  columns: CatColumn[];
  /** 返回 ElInput 实例或原生 textarea */
  getTarget: () => unknown;
  placeholder?: string;
}>();
const emit = defineEmits<{ (e: "insert", value: string): void }>();

const picked = ref<string>();

function onChange(col?: string) {
  picked.value = undefined;
  if (!col) return;
  const target = resolveTextarea(props.getTarget());
  if (!target) {
    ElMessage.warning("请先点击要编辑的输入框,再选择要插入的列");
    return;
  }
  const start = target.selectionStart ?? target.value.length;
  const end = target.selectionEnd ?? target.value.length;
  const { value, caret } = insertAtCursor(target.value, start, end, col);
  emit("insert", value);
  // 值写回后把光标归位并交还焦点(尽力而为,失败不影响插入结果)
  requestAnimationFrame(() => {
    if (document.activeElement !== target) target.focus();
    try {
      target.setSelectionRange(caret, caret);
    } catch {
      /* textarea 已被重渲染等场景忽略 */
    }
  });
}

defineExpose({ onChange, picked });
</script>

<template>
  <ElSelect
    v-model="picked"
    :placeholder="placeholder ?? '插入列'"
    filterable
    style="width: 120px; flex-shrink: 0"
    @update:model-value="onChange"
  >
    <ElOption v-for="c in columns" :key="c.id" :label="c.name" :value="c.name" />
  </ElSelect>
</template>
