<!-- 业务域管理：语义中心 - 业务域(基础分类数据) -->
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
            :perm-create="['module_semantic:domain:create']"
            :perm-delete="['module_semantic:domain:delete']"
            :delete-loading="batchDeleting"
            :create-loading="createLoading"
            @add="handleAdd"
            @delete="handleBatchDelete"
          />
          <!-- 批量启停：选中多行后才可用。停用业务域会影响其下模型的可用性，
               故与批量删除一样要二次确认 -->
          <template v-if="canPatch">
            <ElButton
              size="small" class="ml-2" :disabled="!selectedIds.length"
              :loading="batchPatching" @click="handleBatchStatus(0)"
            >
              批量启用
            </ElButton>
            <ElButton
              size="small" :disabled="!selectedIds.length"
              :loading="batchPatching" @click="handleBatchStatus(1)"
            >
              批量停用
            </ElButton>
          </template>
        </template>
      </FaTableHeader>

      <FaTable
        ref="faTableRef"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @selection-change="onTableSelectionChange"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      width="520px"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @confirm="dialogVisible.type === 'detail' ? handleCloseDialog() : handleSubmit()"
    >
      <template v-if="dialogVisible.type === 'detail'">
        <FaDescriptions :column="1" :data="detailFormData" :items="detailItems" />
      </template>
      <template v-else>
        <FaForm
          :key="formRenderKey"
          ref="dataFormRef"
          v-model="formData"
          :items="dialogFormItems"
          :rules="rules"
          label-suffix=":"
          :label-width="80"
          label-position="right"
          :span="24"
          :show-reset="false"
          :show-submit="false"
        />
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElMessageBox } from "element-plus";
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
import DomainAPI, { type DomainForm, type DomainPageQuery, type DomainTable } from "@/api/module_semantic/domain";

defineOptions({ name: "SemanticDomain", inheritAttrs: false });

const { hasAuth } = useAuth();
/** 批量启停用的是 domain:patch —— 与 update(改内容) 分开，后端也是这么分的 */
const canPatch = computed(() => hasAuth("module_semantic:domain:patch"));
const batchPatching = ref(false);
const STATUS_MAP: Record<number, { type: string; text: string }> = {
  0: { type: "success", text: "启用" },
  1: { type: "danger", text: "停用" },
};

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchForm = ref<Record<string, unknown>>({});
const searchItems = computed<SearchFormItem[]>(() => [
  { label: "域编码", key: "code", type: "input", span: 8, props: { clearable: true, placeholder: "请输入编码" } },
  { label: "域名称", key: "name", type: "input", span: 8, props: { clearable: true, placeholder: "请输入名称" } },
  {
    label: "状态",
    key: "status",
    type: "select",
    span: 8,
    props: {
      clearable: true,
      placeholder: "请选择状态",
      options: [
        { label: "启用", value: 0 },
        { label: "停用", value: 1 },
      ],
    },
  },
]);

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<DomainTable>();
const createLoading = ref(false);

const { dialogVisible } = useCrudDialog();
const detailFormData = ref<DomainTable>({} as DomainTable);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

const initialFormData: DomainForm = { id: undefined, code: "", name: "", order: 0, description: undefined };
const formData = ref<DomainForm>({ ...initialFormData });

const rules = reactive({
  code: [{ required: true, message: "请输入业务域编码", trigger: "blur" }],
  name: [{ required: true, message: "请输入业务域名称", trigger: "blur" }],
});

const detailItems: import("@/components/others/fa-descriptions/index.vue").DescriptionsItem[] = [
  { label: "域编码", prop: "code" },
  { label: "域名称", prop: "name" },
  { label: "排序", prop: "order" },
  { label: "状态", prop: "status", tag: { map: { "0": { type: "success", text: "启用" }, "1": { type: "danger", text: "停用" } } } },
  { label: "描述", prop: "description" },
];

const dialogFormItems = computed<FormItem[]>(() => [
  { label: "域编码", key: "code", type: "input", span: 24, props: { placeholder: "如：revenue", maxlength: 50 } },
  { label: "域名称", key: "name", type: "input", span: 24, props: { placeholder: "如：营收域", maxlength: 100 } },
  { label: "排序", key: "order", type: "number", span: 24, props: { min: 0, controlsPosition: "right", style: { width: "120px" } } },
  { label: "描述", key: "description", type: "input", span: 24, props: { type: "textarea", rows: 3 } },
]);

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } = useCrudForm<DomainForm>({
  formData,
  initialFormData,
  dialogVisible,
  dataFormRef,
  formRenderKey,
  detailApi: DomainAPI.detailDomain,
  createApi: DomainAPI.createDomain,
  updateApi: DomainAPI.updateDomain,
  titles: { create: "新增业务域", update: "修改业务域", detail: "业务域详情" },
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
    await DomainAPI.deleteDomain([id]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    /* 取消 */
  }
}

function buildRowActions(row: DomainTable): TableOperationAction[] {
  const all: TableOperationAction[] = [
    { key: "detail", label: "详情", artType: "view", perm: "module_semantic:domain:detail", run: () => void handleOpenDialog("detail", row.id!) },
    { key: "edit", label: "编辑", artType: "edit", perm: "module_semantic:domain:update", run: () => void handleOpenDialog("update", row.id!) },
    { key: "delete", label: "删除", artType: "delete", perm: "module_semantic:domain:delete", run: () => deleteRow(row.id!) },
  ];
  return all.filter((a) => a.perm != null && hasAuth(a.perm));
}

const { columns, columnChecks, data, loading, pagination, getData, replaceSearchParams, resetSearchParams, handleSizeChange, handleCurrentChange, refreshData, refreshCreate, refreshUpdate, refreshRemove } = useTable({
  core: {
    apiFn: DomainAPI.listDomain,
    apiParams: { page_no: 1, page_size: 10 } as DomainPageQuery,
    columnsFactory: () => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "code", label: "域编码", minWidth: 120, showOverflowTooltip: true },
      { prop: "name", label: "域名称", minWidth: 140, showOverflowTooltip: true },
      { prop: "order", label: "排序", width: 70, align: "center" },
      { prop: "description", label: "描述", minWidth: 160, showOverflowTooltip: true },
      {
        prop: "status",
        label: "状态",
        width: 80,
        formatter: (row: DomainTable) => {
          const m = STATUS_MAP[row.status] ?? STATUS_MAP[0]!;
          return h("span", { class: `el-tag el-tag--${m.type} el-tag--small`, style: "padding:0 8px" }, m.text);
        },
      },
      { prop: "updated_time", label: "更新时间", width: 168, showOverflowTooltip: true },
      {
        prop: "operation",
        label: "操作",
        width: 200,
        fixed: "right",
        align: "center",
        formatter: (row: DomainTable) => renderTableOperationCell(buildRowActions(row), { wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1" }),
      },
    ],
  },
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
/**
 * 批量启停业务域。
 *
 * ★ 停用业务域会影响其下模型的可用性，所以与批量删除一样要二次确认 ——
 * 批量动作的误操作面比单行大得多（选错一行看得见，选错十行看不见）。
 */
async function handleBatchStatus(status: number) {
  const ids = selectedIds.value;
  if (!ids.length) return;
  const label = (STATUS_MAP[status] ?? STATUS_MAP[0]!).text;
  try {
    await ElMessageBox.confirm(
      `将选中的 ${ids.length} 个业务域置为「${label}」？`
      + (status === 1 ? "\n停用后其下模型对使用者不可用。" : ""),
      "批量状态变更",
      { type: "warning", confirmButtonText: label, cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  batchPatching.value = true;
  try {
    await DomainAPI.batchDomain({ ids, status });
    ElMessage.success(`已${label} ${ids.length} 个业务域`);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshData();
  } catch (e: any) {
    ElMessage.error(e?.message || "操作失败");
  } finally {
    batchPatching.value = false;
  }
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (!ids.length) return;
  try {
    await confirmBatchDelete(ids.length);
    batchDeleting.value = true;
    await DomainAPI.deleteDomain(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    /* 取消 */
  } finally {
    batchDeleting.value = false;
  }
}
</script>
