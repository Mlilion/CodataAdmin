<!-- 维度管理：语义中心 - 维度 + 状态机 -->
<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="searchItems"
      :is-expand="false"
      :show-expand="true"
      :show-reset="true"
      :show-search="true"
      @search="handleSearchBarSearch"
      @reset="onResetSearch"
    />

    <ElCard class="fa-table-card" :style="{ 'margin-top': showSearchBar ? '12px' : '0' }">
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      >
        <template #left>
          <FaTableHeaderLeft
            :remove-ids="selectedIds"
            :perm-create="['module_semantic:dimension:create']"
            :perm-delete="['module_semantic:dimension:delete']"
            :delete-loading="batchDeleting"
            :create-loading="createLoading"
            @add="handleAdd"
            @delete="handleBatchDelete"
          />
        </template>
      </FaTableHeader>

      <FaTable
        ref="faTableRef"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        :row-class-name="rowClassName"
        @selection-change="onTableSelectionChange"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
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
      <template v-if="dialogVisible.type === 'detail'">
        <FaDescriptions :column="1" :data="detailFormData" :items="detailItems">
          <template #bind_entity_id="{ value }">
            <ElButton v-if="value != null" type="primary" link @click="goEntity(Number(value))">
              实体 #{{ value }}
            </ElButton>
            <span v-else>-</span>
          </template>
        </FaDescriptions>
      </template>
      <template v-else>
        <FaForm
          :key="formRenderKey"
          ref="dataFormRef"
          v-model="formData"
          :items="dialogFormItems"
          :rules="rules"
          label-suffix=":"
          :label-width="90"
          label-position="right"
          :span="24"
          :show-reset="false"
          :show-submit="false"
        >
          <template #bind_entity_id>
            <EntitySelect
              v-model="formData.bind_entity_id"
              @update:model-value="() => (formData.bind_field_code = undefined)"
            />
          </template>
          <template #bind_field_code>
            <FieldSelect v-model="formData.bind_field_code" :entity-id="formData.bind_entity_id" />
          </template>
          <template #aliases>
            <AliasTagsSelect v-model="formData.aliases" placeholder="输入别名后回车,如:省份" />
          </template>
        </FaForm>
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useTable } from "@/hooks/core/useTable";
import { useCrudDialog } from "@/hooks/core/useCrudDialog";
import { useCrudForm } from "@/hooks/core/useCrudForm";
import { useTableSelection } from "@/hooks/core/useTableSelection";
import { confirmDelete, confirmBatchDelete } from "@/hooks/core/useConfirm";
import { renderTableOperationCell, type TableOperationAction } from "@utils";
import { useAuth } from "@/hooks/core/useAuth";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import type FaForm from "@/components/forms/fa-form/index.vue";
import EntitySelect from "../_shared/EntitySelect.vue";
import FieldSelect from "../_shared/FieldSelect.vue";
import AliasTagsSelect from "../_shared/AliasTagsSelect.vue";
import { useNavigateToNode } from "../_shared/useNavigateToNode";
import DimensionAPI, {
  type DimensionForm,
  type DimensionPageQuery,
  type DimensionTable,
} from "@/api/module_semantic/dimension";

defineOptions({ name: "SemanticDimension", inheritAttrs: false });

const { goEntity } = useNavigateToNode();
const { hasAuth } = useAuth();
const route = useRoute();
// 跨页跳转高亮:其他语义页通过带 ?focus=<id> 跳转到本页,
// 命中该 id 的行加高亮样式 class,并提示用户。
const focusId = ref<number | null>(null);
function rowClassName({ row }: { row: Record<string, any>; rowIndex: number }) {
  return focusId.value != null && (row as DimensionTable).id === focusId.value ? "fa-focus-row" : "";
}

const STATUS_MAP: Record<number, { type: string; text: string }> = {
  0: { type: "info", text: "草稿" },
  1: { type: "warning", text: "待审" },
  2: { type: "success", text: "已发布" },
  3: { type: "danger", text: "已归档" },
};

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchForm = ref<Record<string, unknown>>({});
const searchItems = computed<SearchFormItem[]>(() => [
  { label: "维度编码", key: "code", type: "input", span: 8, props: { clearable: true, placeholder: "请输入编码" } },
  { label: "维度名称", key: "name", type: "input", span: 8, props: { clearable: true, placeholder: "请输入名称" } },
  {
    label: "状态",
    key: "status",
    type: "select",
    span: 8,
    props: {
      clearable: true,
      placeholder: "请选择状态",
      options: [
        { label: "草稿", value: 0 },
        { label: "待审", value: 1 },
        { label: "已发布", value: 2 },
        { label: "已归档", value: 3 },
      ],
    },
  },
]);

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<DimensionTable>();
const createLoading = ref(false);

const { dialogVisible } = useCrudDialog();
const detailFormData = ref<DimensionTable>({} as DimensionTable);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

const initialFormData: DimensionForm = {
  id: undefined,
  code: "",
  name: "",
  dimension_type: undefined,
  description: undefined,
  bind_entity_id: undefined,
  bind_field_code: undefined,
  // 显式列出:useCrudForm 重置用 Object.assign,不声明则上次编辑的别名会残留到下次新增
  aliases: undefined,
};
const formData = ref<DimensionForm>({ ...initialFormData });

const rules = reactive({
  code: [{ required: true, message: "请输入维度编码", trigger: "blur" }],
  name: [{ required: true, message: "请输入维度名称", trigger: "blur" }],
});

const detailItems: import("@/components/others/fa-descriptions/index.vue").DescriptionsItem[] = [
  { label: "维度编码", prop: "code" },
  { label: "维度名称", prop: "name" },
  { label: "维度类型", prop: "dimension_type" },
  { label: "绑定实体", prop: "bind_entity_id", slot: "bind_entity_id" },
  {
    label: "状态",
    prop: "status",
    tag: { map: { "0": { type: "info", text: "草稿" }, "1": { type: "warning", text: "待审" }, "2": { type: "success", text: "已发布" }, "3": { type: "danger", text: "已归档" } } },
  },
  { label: "描述", prop: "description" },
  { label: "创建时间", prop: "created_time" },
  { label: "更新时间", prop: "updated_time" },
];

const dialogFormItems = computed<FormItem[]>(() => [
  { label: "维度编码", key: "code", type: "input", span: 24, props: { placeholder: "字母/数字编码", maxlength: 50 } },
  { label: "维度名称", key: "name", type: "input", span: 24, props: { placeholder: "请输入名称", maxlength: 100 } },
  {
    label: "维度类型",
    key: "dimension_type",
    type: "select",
    span: 24,
    props: {
      clearable: true,
      filterable: true,
      allowCreate: true,
      defaultFirstOption: true,
      placeholder: "选择或输入维度类型",
      options: [
        { label: "时间", value: "时间" },
        { label: "地区", value: "地区" },
        { label: "渠道", value: "渠道" },
        { label: "产品", value: "产品" },
        { label: "客户", value: "客户" },
        { label: "组织", value: "组织" },
        { label: "状态", value: "状态" },
        { label: "其他", value: "其他" },
      ],
    },
  },
  { label: "绑定实体", key: "bind_entity_id", span: 12 },
  { label: "绑定字段", key: "bind_field_code", span: 12 },
  { label: "别名", key: "aliases", span: 24 },
  { label: "描述", key: "description", type: "input", span: 24, props: { type: "textarea", rows: 3 } },
]);

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } = useCrudForm<DimensionForm>({
  formData,
  initialFormData,
  dialogVisible,
  dataFormRef,
  formRenderKey,
  detailApi: DimensionAPI.detailDimension,
  createApi: DimensionAPI.createDimension,
  updateApi: DimensionAPI.updateDimension,
  titles: { create: "新增维度", update: "修改维度", detail: "维度详情" },
  detailFormData,
  onCreateSuccess: async () => {
    await refreshCreate();
  },
  onUpdateSuccess: async () => {
    await refreshUpdate();
  },
});

async function handleAdd() {
  createLoading.value = true;
  try {
    await handleOpenDialog("create");
  } finally {
    createLoading.value = false;
  }
}

async function deleteRow(id: number) {
  try {
    await confirmDelete();
    await DimensionAPI.deleteDimension([id]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    /* 取消 */
  }
}

async function changeStatus(row: DimensionTable, target: number) {
  try {
    await ElMessageBox.confirm(`确认将「${row.name}」置为「${(STATUS_MAP[target] ?? STATUS_MAP[0]!).text}」?`, "状态流转", { type: "warning" });
    await DimensionAPI.changeStatus(row.id!, target);
    ElMessage.success("状态已更新");
    await refreshData();
  } catch (e) {
    if (e !== "cancel") ElMessage.error("状态流转失败");
  }
}

function buildRowActions(row: DimensionTable): TableOperationAction[] {
  const s = row.status;
  const all: TableOperationAction[] = [
    { key: "detail", label: "详情", artType: "view", perm: "module_semantic:dimension:detail", run: () => void handleOpenDialog("detail", row.id!) },
    { key: "edit", label: "编辑", artType: "edit", perm: "module_semantic:dimension:update", run: () => void handleOpenDialog("update", row.id!) },
  ];
  if (s === 0) all.push({ key: "submit", label: "提审", artType: "view", perm: "module_semantic:dimension:status", run: () => changeStatus(row, 1) });
  if (s === 1) all.push({ key: "publish", label: "发布", artType: "view", perm: "module_semantic:dimension:status", run: () => changeStatus(row, 2) });
  if (s === 2) all.push({ key: "withdraw", label: "撤回", artType: "view", perm: "module_semantic:dimension:status", run: () => changeStatus(row, 1) });
  all.push({ key: "delete", label: "删除", artType: "delete", perm: "module_semantic:dimension:delete", run: () => deleteRow(row.id!) });
  return all.filter((a) => a.perm != null && hasAuth(a.perm));
}

const { columns, columnChecks, data, loading, pagination, getData, replaceSearchParams, resetSearchParams, handleSizeChange, handleCurrentChange, refreshData, refreshCreate, refreshUpdate, refreshRemove } = useTable({
  core: {
    apiFn: DimensionAPI.listDimension,
    apiParams: { page_no: 1, page_size: 10 } as DimensionPageQuery,
    columnsFactory: () => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "code", label: "编码", minWidth: 120, showOverflowTooltip: true },
      { prop: "name", label: "名称", minWidth: 140, showOverflowTooltip: true },
      { prop: "dimension_type", label: "类型", minWidth: 100, showOverflowTooltip: true },
      { prop: "description", label: "描述", minWidth: 160, showOverflowTooltip: true },
      {
        prop: "status",
        label: "状态",
        width: 90,
        formatter: (row: DimensionTable) => {
          const m = STATUS_MAP[row.status] ?? STATUS_MAP[0]!;
          return h("span", { class: `el-tag el-tag--${m.type} el-tag--small`, style: "padding:0 8px" }, m.text);
        },
      },
      { prop: "updated_time", label: "更新时间", width: 168, showOverflowTooltip: true },
      {
        prop: "operation",
        label: "操作",
        width: 220,
        fixed: "right",
        align: "center",
        formatter: (row: DimensionTable) =>
          renderTableOperationCell(buildRowActions(row), { wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1" }),
      },
    ],
  },
});

onMounted(() => {
  const raw = route.query.focus;
  const id = Number(Array.isArray(raw) ? raw[0] : raw);
  if (raw != null && !Number.isNaN(id)) {
    focusId.value = id;
    ElMessage.info(`已定位到维度 #${id},在列表中高亮显示`);
  }
});

async function handleSearchBarSearch(params: Record<string, unknown>) {
  replaceSearchParams({
    code: params.code as string | undefined,
    name: params.name as string | undefined,
    status: params.status as number | undefined,
  });
  getData();
}
function onResetSearch() {
  searchForm.value = {};
  void resetSearchParams();
}
async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (!ids.length) return;
  try {
    await confirmBatchDelete(ids.length);
    batchDeleting.value = true;
    await DimensionAPI.deleteDimension(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    /* 取消 */
  } finally {
    batchDeleting.value = false;
  }
}
</script>

<style scoped lang="scss">
:deep(.fa-focus-row) {
  --el-table-tr-bg-color: var(--el-color-primary-light-9);
  background-color: var(--el-color-primary-light-9);
}
</style>
