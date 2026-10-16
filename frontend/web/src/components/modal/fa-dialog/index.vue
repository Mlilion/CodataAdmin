<template>
  <ElDialog
    v-model="visible"
    :width="width"
    :draggable="draggable"
    :fullscreen="fullscreen"
    :show-close="false"
    :class="dialogClass"
    :modal-class="modalClass"
    align-center
    destroy-on-close
    v-bind="dialogAttrs"
    @close="emit('close')"
    @closed="emit('closed')"
    @opened="emit('opened')"
  >
    <template #header="{ titleId, titleClass, close }">
      <div class="core-overlay-dialog__header">
        <span :id="titleId" :class="titleClass">{{ title }}</span>
        <div class="core-overlay-dialog__actions">
          <ElTooltip :content="fullscreen ? '还原' : '全屏'" placement="top">
            <FaIconButton
              class="core-overlay-icon-btn"
              :icon="fullscreen ? 'ri:fullscreen-exit-line' : 'ri:fullscreen-fill'"
              @click="fullscreen = !fullscreen"
            />
          </ElTooltip>
          <ElTooltip content="关闭" placement="top">
            <FaIconButton class="core-overlay-icon-btn" icon="ri:close-line" @click="close" />
          </ElTooltip>
        </div>
      </div>
    </template>
    <slot />
    <template v-if="$slots.footer" #footer>
      <slot name="footer" />
    </template>
    <template v-else-if="formMode" #footer>
      <div class="fa-dialog-footer" :style="'padding-right: var(--el-dialog-padding-primary)'">
        <ElButton v-if="formMode !== 'detail'" type="primary" plain @click="emit('cancel')">
          {{ cancelText }}
        </ElButton>
        <ElButton type="primary" :loading="confirmLoading" @click="emit('confirm')">
          {{ confirmText }}
        </ElButton>
      </div>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import type { DialogProps } from "element-plus";
import { computed, ref, useAttrs, watch } from "vue";
import FaIconButton from "@/components/widget/fa-icon-button/index.vue";

defineOptions({ name: "FaDialog", inheritAttrs: false });

interface Props {
  modelValue: boolean;
  title?: string;
  width?: string | number;
  /** 默认可拖拽；全屏时 Element Plus 会限制拖拽 */
  draggable?: boolean;
  /** 透传到 el-dialog 的 class */
  dialogClass?: string;
  /** 遮罩层自定义 class */
  modalClass?: string;
  /** 表单模式：detail 仅显示确定；create/update 显示取消+确定 */
  formMode?: "detail" | "create" | "update";
  /** 确定按钮 loading 状态 */
  confirmLoading?: boolean;
  /** 确定按钮文本 */
  confirmText?: string;
  /** 取消按钮文本 */
  cancelText?: string;
}

const props = withDefaults(defineProps<Props>(), {
  draggable: true,
  confirmText: "确定",
  cancelText: "取消",
});

interface Emits {
  "update:modelValue": [v: boolean];
  close: [];
  closed: [];
  opened: [];
  "fullscreen-change": [isFullscreen: boolean];
  /** 点击取消按钮 */
  cancel: [];
  /** 点击确定按钮 */
  confirm: [];
}

const emit = defineEmits<Emits>();

const attrs = useAttrs();
const fullscreen = ref(false);

watch(fullscreen, (newVal) => {
  emit("fullscreen-change", newVal);
});

const dialogClass = computed(() => {
  const a = attrs.class;
  return [props.dialogClass, a].filter(Boolean);
});

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit("update:modelValue", v),
});

/** 透传除 modelValue 外的 el-dialog 属性（如 top、modal-class、append-to-body 等） */
const dialogAttrs = computed(() => {
  const a = { ...attrs } as Record<string, unknown>;
  delete a.class;
  return a as Partial<Omit<DialogProps, "modelValue">>;
});
</script>

<style scoped lang="scss">
.core-overlay-dialog__header {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding-right: 4px;
}

.fa-dialog-footer {
  display: flex;
  gap: 8px;
  justify-content: flex-end;

  :deep(.el-button) {
    transition: all 0.2s ease;

    &:hover {
      box-shadow: 0 4px 12px rgb(0 0 0 / 10%);
      transform: translateY(-2px);
    }
  }
}

.core-overlay-dialog__actions {
  display: inline-flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
  margin-left: auto;

  :deep(.core-overlay-icon-btn) {
    min-width: 32px;
    padding: 6px;
    border-radius: var(--el-border-radius-base);

    &:hover {
      color: var(--theme-color);
    }
  }
}
</style>
