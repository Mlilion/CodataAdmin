<!-- 语义模型：语义中心 - 实体 JOIN 关系(L3) + 关系图 -->
<template>
  <div class="fa-full-height">
    <ElCard class="fa-table-card">
      <div class="flex items-center justify-between mb-3">
        <ElRadioGroup v-model="viewMode" size="small">
          <ElRadioButton value="list">列表</ElRadioButton>
          <ElRadioButton value="graph">关系图</ElRadioButton>
        </ElRadioGroup>
        <div class="flex gap-2">
          <ElButton v-if="viewMode === 'list' && hasAuth('module_semantic:join:create')" type="primary" size="small" @click="handleAdd">
            新建关系
          </ElButton>
          <ElButton size="small" @click="refreshAll">刷新</ElButton>
        </div>
      </div>

      <!-- 列表视图 -->
      <FaTable
        v-if="viewMode === 'list'"
        ref="faTableRef"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />

      <!-- 关系图视图 -->
      <div v-else class="join-graph">
        <VueFlow :nodes="flowNodes" :edges="flowEdges" :default-viewport="{ zoom: 0.9 }" fit-view-on-init>
          <Background />
          <Controls />
        </VueFlow>
        <ElEmpty v-if="!flowNodes.length" description="暂无关系(先创建 JOIN)" :image-size="60" class="join-graph__empty" />
      </div>
    </ElCard>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      width="560px"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @confirm="dialogVisible.type === 'detail' ? handleCloseDialog() : handleSubmit()"
    >
      <ElForm ref="formEl" :model="formData" :rules="rules" label-width="100px">
        <ElFormItem label="左实体" prop="from_entity_id">
          <EntitySelect v-model="formData.from_entity_id" />
        </ElFormItem>
        <ElFormItem label="右实体" prop="to_entity_id">
          <EntitySelect v-model="formData.to_entity_id" />
        </ElFormItem>
        <ElFormItem label="JOIN 条件" prop="on_expr">
          <OnExprBuilder
            :from-entity-id="formData.from_entity_id"
            :to-entity-id="formData.to_entity_id"
            :model-value="formData.on_expr"
            @update:model-value="(v: string | undefined) => (formData.on_expr = v ?? '')"
          />
        </ElFormItem>
        <ElFormItem label="关系类型" prop="relationship_type">
          <ElSelect v-model="formData.relationship_type" class="w-full">
            <ElOption label="多对一 (many_to_one)" value="many_to_one" />
            <ElOption label="一对多 (one_to_many)" value="one_to_many" />
            <ElOption label="一对一 (one_to_one)" value="one_to_one" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="JOIN 方式" prop="join_type">
          <ElSelect v-model="formData.join_type" class="w-full">
            <ElOption label="LEFT" value="left" />
            <ElOption label="INNER" value="inner" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="说明">
          <ElInput v-model="formData.description" type="textarea" :rows="2" />
        </ElFormItem>
      </ElForm>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { VueFlow } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import { Controls } from "@vue-flow/controls";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";
import { useTable } from "@/hooks/core/useTable";
import { useCrudDialog } from "@/hooks/core/useCrudDialog";
import { useTableSelection } from "@/hooks/core/useTableSelection";
import { confirmDelete } from "@/hooks/core/useConfirm";
import { renderTableOperationCell, type TableOperationAction } from "@utils";
import { useAuth } from "@/hooks/core/useAuth";
import JoinAPI, { type JoinForm, type JoinGraph, type JoinPageQuery, type JoinTable } from "@/api/module_semantic/join";
import EntitySelect from "../_shared/EntitySelect.vue";
import OnExprBuilder from "./OnExprBuilder.vue";

defineOptions({ name: "SemanticJoin", inheritAttrs: false });

const { hasAuth } = useAuth();
const REL_MAP: Record<string, string> = { many_to_one: "多对一", one_to_many: "一对多", one_to_one: "一对一" };
const STATUS_MAP: Record<number, { type: string; text: string }> = {
  0: { type: "info", text: "草稿" },
  1: { type: "warning", text: "待审" },
  2: { type: "success", text: "已发布" },
  3: { type: "danger", text: "已归档" },
};

const viewMode = ref<"list" | "graph">("list");
const faTableRef = ref();
useTableSelection<JoinTable>();

// 关系图
const graph = ref<JoinGraph>({ nodes: [], edges: [] });
const flowNodes = computed(() =>
  graph.value.nodes.map((n, i) => ({
    id: String(n.id),
    position: { x: (i % 4) * 220, y: Math.floor(i / 4) * 140 },
    data: { label: `${n.name}\n(${n.entity_type})` },
    label: `${n.name} (${n.entity_type})`,
  }))
);
const flowEdges = computed(() =>
  graph.value.edges.map((e) => ({
    id: String(e.id),
    source: String(e.from_entity_id),
    target: String(e.to_entity_id),
    label: REL_MAP[e.relationship_type] || e.relationship_type,
    animated: true,
  }))
);
async function loadGraph() {
  const res = await JoinAPI.graph();
  graph.value = res.data.data || { nodes: [], edges: [] };
}

// 表单
const { dialogVisible } = useCrudDialog();
const formEl = ref();
const submitLoading = ref(false);
const initialForm: JoinForm = {
  id: undefined,
  from_entity_id: undefined,
  to_entity_id: undefined,
  on_expr: "",
  relationship_type: "many_to_one",
  join_type: "left",
  description: undefined,
};
const formData = ref<JoinForm>({ ...initialForm });
const rules = {
  from_entity_id: [{ required: true, message: "请选择左实体", trigger: "change" }],
  to_entity_id: [{ required: true, message: "请选择右实体", trigger: "change" }],
  on_expr: [{ required: true, message: "请生成或输入 JOIN 条件", trigger: "change" }],
};

function handleAdd() {
  dialogVisible.type = "create";
  dialogVisible.title = "新建 JOIN 关系";
  formData.value = { ...initialForm };
  dialogVisible.visible = true;
}
async function handleEdit(id: number) {
  const res = await JoinAPI.detailJoin(id);
  const d = res.data.data;
  dialogVisible.type = "update";
  dialogVisible.title = "编辑 JOIN 关系";
  formData.value = {
    id: d.id,
    from_entity_id: d.from_entity_id,
    to_entity_id: d.to_entity_id,
    on_expr: d.on_expr,
    relationship_type: d.relationship_type,
    join_type: d.join_type,
    description: d.description,
  };
  dialogVisible.visible = true;
}
function handleCloseDialog() {
  dialogVisible.visible = false;
}
async function handleSubmit() {
  const valid = await formEl.value?.validate?.().catch(() => false);
  if (!valid) return;
  submitLoading.value = true;
  try {
    if (formData.value.id) {
      await JoinAPI.updateJoin(formData.value.id, formData.value);
    } else {
      await JoinAPI.createJoin(formData.value);
    }
    ElMessage.success("保存成功");
    dialogVisible.visible = false;
    await refreshAll();
  } catch {
    /* 错误已全局处理 */
  } finally {
    submitLoading.value = false;
  }
}

async function deleteRow(id: number) {
  try {
    await confirmDelete();
    await JoinAPI.deleteJoin([id]);
    await refreshAll();
  } catch {
    /* 取消 */
  }
}
async function changeStatus(row: JoinTable, target: number) {
  try {
    await ElMessageBox.confirm(`确认将该关系置为「${(STATUS_MAP[target] ?? STATUS_MAP[0]!).text}」?`, "状态流转", { type: "warning" });
    await JoinAPI.changeStatus(row.id!, target);
    ElMessage.success("状态已更新");
    await refreshData();
  } catch (e) {
    if (e !== "cancel") ElMessage.error("状态流转失败");
  }
}

function buildRowActions(row: JoinTable): TableOperationAction[] {
  const s = row.status;
  const all: TableOperationAction[] = [
    { key: "edit", label: "编辑", artType: "edit", perm: "module_semantic:join:update", run: () => handleEdit(row.id!) },
  ];
  if (s === 0) all.push({ key: "submit", label: "提审", artType: "view", perm: "module_semantic:join:status", run: () => changeStatus(row, 1) });
  if (s === 1) all.push({ key: "publish", label: "发布", artType: "view", perm: "module_semantic:join:status", run: () => changeStatus(row, 2) });
  all.push({ key: "delete", label: "删除", artType: "delete", perm: "module_semantic:join:delete", run: () => deleteRow(row.id!) });
  return all.filter((a) => a.perm != null && hasAuth(a.perm));
}

const { columns, data, loading, pagination, getData, handleSizeChange, handleCurrentChange, refreshData } = useTable({
  core: {
    apiFn: JoinAPI.listJoin,
    apiParams: { page_no: 1, page_size: 10 } as JoinPageQuery,
    columnsFactory: () => [
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "from_entity", label: "左实体", minWidth: 140, formatter: (row: JoinTable) => row.from_entity?.name || row.from_entity_id },
      { prop: "to_entity", label: "右实体", minWidth: 140, formatter: (row: JoinTable) => row.to_entity?.name || row.to_entity_id },
      { prop: "on_expr", label: "JOIN条件", minWidth: 200, showOverflowTooltip: true },
      { prop: "relationship_type", label: "关系", width: 90, formatter: (row: JoinTable) => REL_MAP[row.relationship_type] || row.relationship_type },
      { prop: "join_type", label: "方式", width: 80 },
      {
        prop: "status",
        label: "状态",
        width: 90,
        formatter: (row: JoinTable) => {
          const m = STATUS_MAP[row.status] ?? STATUS_MAP[0]!;
          return h("span", { class: `el-tag el-tag--${m.type} el-tag--small`, style: "padding:0 8px" }, m.text);
        },
      },
      {
        prop: "operation",
        label: "操作",
        width: 200,
        fixed: "right",
        align: "center",
        formatter: (row: JoinTable) => renderTableOperationCell(buildRowActions(row), { wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1" }),
      },
    ],
  },
});

async function refreshAll() {
  await Promise.all([refreshData(), loadGraph()]);
}

watch(viewMode, (m) => {
  if (m === "graph") loadGraph();
});

onMounted(() => {
  getData();
});
</script>

<style scoped lang="scss">
.join-graph {
  position: relative;
  height: 60vh;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  &__empty {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
}
</style>
