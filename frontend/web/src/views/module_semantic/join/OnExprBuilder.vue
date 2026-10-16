<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import EntityAPI, { type EntityTable } from "@/api/module_semantic/entity";
import { buildOnExpr, parseOnExpr, type JoinCondRow } from "./onExpr";

/**
 * JOIN 条件生成器:结构化(左右字段下拉 → 拼 `实体code.物理列 = 实体code.物理列`)
 * + 高级模式(手工文本,存量 CAST 等表达式回显落这里,原文绝不被改写)。
 */
const props = defineProps<{ fromEntityId?: number; toEntityId?: number; modelValue?: string }>();
const emit = defineEmits<{ (e: "update:modelValue", v: string | undefined): void }>();

const leftEntity = ref<EntityTable | null>(null);
const rightEntity = ref<EntityTable | null>(null);
let leftToken = 0;
let rightToken = 0;

async function loadSide(id: number | undefined, side: "left" | "right") {
  const token = side === "left" ? ++leftToken : ++rightToken;
  if (side === "left") leftEntity.value = null;
  else rightEntity.value = null;
  if (!id) return;
  try {
    const res = await EntityAPI.detailEntity(id);
    const data = res.data.data ?? null;
    if (side === "left" ? token === leftToken : token === rightToken) {
      if (side === "left") leftEntity.value = data;
      else rightEntity.value = data;
    }
  } catch (e) {
    console.warn("[OnExprBuilder] 加载实体字段失败", e);
  }
}
watch(() => props.fromEntityId, (id) => void loadSide(id, "left"), { immediate: true });
watch(() => props.toEntityId, (id) => void loadSide(id, "right"), { immediate: true });

const rows = ref<JoinCondRow[]>([{ left: "", right: "" }]);
const mode = ref<"struct" | "advanced">("struct");
/** 用户改过结构化行/高级文本后,停止自动回显,避免覆盖输入 */
const userTouched = ref(false);

const codesReady = computed(() => !!leftEntity.value?.code && !!rightEntity.value?.code);

function fieldOptions(entity: EntityTable | null) {
  const seen = new Set<string>();
  const out: { label: string; value: string }[] = [];
  for (const f of entity?.fields ?? []) {
    // on_expr 进 SQL 要用物理列名;未登记物理列的老字段回退 code
    const v = f.physical_column || f.code;
    if (!v || seen.has(v)) continue;
    seen.add(v);
    out.push({ label: `${f.name} (${v})`, value: v });
  }
  return out;
}
const leftFieldOptions = computed(() => fieldOptions(leftEntity.value));
const rightFieldOptions = computed(() => fieldOptions(rightEntity.value));

const preview = computed(() =>
  codesReady.value ? buildOnExpr(rows.value, leftEntity.value!.code, rightEntity.value!.code) : ""
);
const tooLong = computed(() => (props.modelValue?.length ?? 0) > 500);

function emitStruct() {
  if (!codesReady.value) return;
  emit("update:modelValue", buildOnExpr(rows.value, leftEntity.value!.code, rightEntity.value!.code) || undefined);
}
function onRowEdit() {
  userTouched.value = true;
  emitStruct();
}
function addRow() {
  userTouched.value = true;
  rows.value.push({ left: "", right: "" });
  emitStruct();
}
function removeRow(index: number) {
  rows.value.splice(index, 1);
  if (!rows.value.length) rows.value.push({ left: "", right: "" });
  if (userTouched.value) emitStruct();
}

/** 回显:实体就绪且有值时尝试解析成结构化行;失败静默落高级模式,零 emit */
function tryHydrate() {
  if (userTouched.value) return;
  const expr = (props.modelValue ?? "").trim();
  if (!codesReady.value) return;
  if (!expr) {
    mode.value = "struct";
    rows.value = [{ left: "", right: "" }];
    return;
  }
  const parsed = parseOnExpr(expr, leftEntity.value!.code, rightEntity.value!.code);
  if (parsed) {
    rows.value = parsed;
    mode.value = "struct";
  } else {
    mode.value = "advanced";
  }
}
watch(() => [props.modelValue, codesReady.value], tryHydrate, { immediate: true });
// 换实体(含重开弹窗):停止回显锁、清行,重新尝试回显;
// 旧表达式对新实体多半解析失败 → 高级模式,与改造前行为一致:不清用户数据。
// expr 为空时直接归位结构化(重开「新建」时 codesReady 尚 false,tryHydrate 不会处理)。
watch(
  () => [props.fromEntityId, props.toEntityId],
  () => {
    userTouched.value = false;
    rows.value = [{ left: "", right: "" }];
    if (!(props.modelValue ?? "").trim()) mode.value = "struct";
    else tryHydrate();
  }
);

const advancedText = computed({
  get: () => props.modelValue ?? "",
  set: (v: string) => {
    userTouched.value = true;
    emit("update:modelValue", v || undefined);
  },
});

function trySwitchToStruct() {
  const parsed = codesReady.value
    ? parseOnExpr(props.modelValue, leftEntity.value!.code, rightEntity.value!.code)
    : null;
  if (parsed) {
    rows.value = parsed;
    mode.value = "struct";
  } else {
    ElMessage.warning("当前条件无法转成结构化行,请继续在高级模式编辑");
  }
}

defineExpose({
  rows,
  mode,
  userTouched,
  codesReady,
  leftEntity,
  rightEntity,
  leftFieldOptions,
  rightFieldOptions,
  preview,
  onRowEdit,
  addRow,
  removeRow,
  tryHydrate,
  trySwitchToStruct,
});
</script>

<template>
  <div class="on-expr-builder">
    <div class="on-expr-builder__head">
      <ElRadioGroup v-model="mode" size="small">
        <ElRadioButton value="struct">结构化</ElRadioButton>
        <ElRadioButton value="advanced">高级(手工)</ElRadioButton>
      </ElRadioGroup>
      <ElButton v-if="mode === 'struct'" size="small" link type="primary" @click="addRow">
        添加条件
      </ElButton>
    </div>

    <template v-if="mode === 'struct'">
      <div v-if="!codesReady" class="on-expr-builder__tip">先选择左右实体,即可从两侧字段下拉生成条件</div>
      <div v-for="(row, i) in rows" :key="i" class="on-expr-builder__row">
        <ElSelect
          v-model="row.left"
          size="small"
          filterable
          allow-create
          default-first-option
          clearable
          placeholder="左字段"
          :disabled="!leftEntity"
          @change="onRowEdit"
        >
          <ElOption v-for="o in leftFieldOptions" :key="o.value" :label="o.label" :value="o.value" />
        </ElSelect>
        <span class="on-expr-builder__eq">=</span>
        <ElSelect
          v-model="row.right"
          size="small"
          filterable
          allow-create
          default-first-option
          clearable
          placeholder="右字段"
          :disabled="!rightEntity"
          @change="onRowEdit"
        >
          <ElOption v-for="o in rightFieldOptions" :key="o.value" :label="o.label" :value="o.value" />
        </ElSelect>
        <ElButton
          size="small"
          link
          type="danger"
          :disabled="rows.length <= 1"
          @click="removeRow(i)"
        >
          删除
        </ElButton>
      </div>
      <div v-if="preview" class="on-expr-builder__preview">{{ preview }}</div>
      <div v-if="tooLong" class="on-expr-builder__warn">JOIN 条件超过 500 字符,后端将拒绝保存</div>
    </template>
    <template v-else>
      <ElInput
        v-model="advancedText"
        type="textarea"
        :rows="2"
        placeholder="如 order_revenue.user_id = dim_user.user_id(多条件用 AND,不要写注释/子查询)"
      />
      <ElButton size="small" link type="primary" @click="trySwitchToStruct">
        尝试切回结构化
      </ElButton>
    </template>
  </div>
</template>

<style scoped lang="scss">
.on-expr-builder {
  width: 100%;

  &__head {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
  }

  &__row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }

  &__eq {
    color: var(--el-text-color-secondary);
  }

  &__tip {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    margin-bottom: 6px;
  }

  &__preview {
    margin-top: 4px;
    padding: 4px 8px;
    font-family: var(--el-font-family-mono, monospace);
    font-size: 12px;
    color: var(--el-text-color-secondary);
    background: var(--el-fill-color-light);
    border-radius: 4px;
    word-break: break-all;
  }

  &__warn {
    margin-top: 4px;
    font-size: 12px;
    color: var(--el-color-warning);
  }
}
</style>
