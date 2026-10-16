<!-- 业务实体管理：语义中心 - 业务实体(含业务字段子表) + 状态机 -->
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
            :perm-create="['module_semantic:entity:create']"
            :perm-delete="['module_semantic:entity:delete']"
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
      width="880px"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @confirm="dialogVisible.type === 'detail' ? handleCloseDialog() : handleSubmit()"
    >
      <template v-if="dialogVisible.type === 'detail'">
        <FaDescriptions :column="2" :data="detailFormData" :items="detailItems" max-height="72vh">
          <template #fields>
            <ElTable :data="detailFormData.fields || []" size="small" border>
              <ElTableColumn prop="code" label="编码" width="120" />
              <ElTableColumn prop="name" label="业务名" width="120" />
              <ElTableColumn prop="physical_column" label="物理列" width="120" />
              <ElTableColumn prop="field_role" label="角色" width="90" />
              <ElTableColumn prop="data_type" label="类型" width="90" />
              <ElTableColumn prop="description" label="业务含义" show-overflow-tooltip />
            </ElTable>
          </template>
        </FaDescriptions>
      </template>
      <template v-else>
        <FaForm
          :key="formRenderKey"
          scrollbar
          max-height="72vh"
          ref="dataFormRef"
          v-model="formData"
          :items="dialogFormItems"
          :rules="rules"
          label-suffix=":"
          :label-width="100"
          label-position="right"
          :span="12"
          :gutter="16"
          :show-reset="false"
          :show-submit="false"
        >
          <template #domain_code>
            <DomainSelect v-model="formData.domain_code" />
          </template>
          <template #physical_table_id>
            <PhysicalTableSelect
              :model-value="formData.physical_table_id"
              @update:model-value="handlePhysicalTableChange"
              @update:meta="handlePhysicalTableMeta"
              @update:columns="handlePhysicalTableColumns"
            />
          </template>
          <template #grain>
            <PhysicalColumnNamesSelect
              v-model="formData.grain"
              :columns="physicalColumns"
              multiple
              :disabled="!physicalColumns.length"
              :placeholder="physicalColumns.length ? '选择粒度键(可多选,组合唯一)' : '请先选择物理表'"
            />
          </template>
          <template #default_time_field>
            <PhysicalColumnNamesSelect
              v-model="formData.default_time_field"
              :columns="physicalColumns"
              prefer-time
              :disabled="!physicalColumns.length"
              :placeholder="physicalColumns.length ? '选择默认时间字段' : '请先选择物理表'"
            />
          </template>
          <template #fields>
            <div class="field-editor">
              <div class="field-editor__head">
                <span>业务字段</span>
                <div class="field-editor__actions">
                  <ElButton
                    v-if="dialogVisible.type === 'update' && formData.physical_table_id"
                    size="small"
                    plain
                    :loading="syncFieldsLoading"
                    @click="syncFieldsFromTable"
                  >
                    从物理表同步字段
                  </ElButton>
                  <ElButton size="small" type="primary" plain @click="addField">新增字段</ElButton>
                </div>
              </div>
              <ElTable :data="formData.fields || []" size="small" border>
                <ElTableColumn label="编码" width="120">
                  <template #default="{ row }"
                    ><ElInput v-model="row.code" size="small" placeholder="字段编码"
                  /></template>
                </ElTableColumn>
                <ElTableColumn label="业务名" width="120">
                  <template #default="{ row }"
                    ><ElInput v-model="row.name" size="small" placeholder="业务名"
                  /></template>
                </ElTableColumn>
                <ElTableColumn label="物理列" width="160">
                  <template #default="{ row }">
                    <PhysicalColumnSelect
                      :model-value="row.physical_column_id"
                      :table-id="formData.physical_table_id"
                      :columns="physicalColumns"
                      @update:model-value="(v) => (row.physical_column_id = v)"
                      @update:meta="(m) => applyFieldColumnMeta(row, m)"
                    />
                  </template>
                </ElTableColumn>
                <ElTableColumn label="角色" width="110">
                  <template #default="{ row }">
                    <ElSelect v-model="row.field_role" size="small">
                      <ElOption label="度量" value="measure" />
                      <ElOption label="维度" value="dimension" />
                      <ElOption label="时间" value="time" />
                      <ElOption label="标识" value="id" />
                    </ElSelect>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="可见性" width="110">
                  <template #default="{ row }">
                    <ElSelect v-model="row.visibility" size="small">
                      <ElOption label="公开" value="public" />
                      <ElOption label="内部" value="internal" />
                      <ElOption label="隐藏" value="hidden" />
                    </ElSelect>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="业务含义" min-width="160">
                  <template #default="{ row }"
                    ><ElInput v-model="row.description" size="small" placeholder="业务含义"
                  /></template>
                </ElTableColumn>
                <ElTableColumn label="操作" width="60" align="center">
                  <template #default="{ $index }">
                    <ElButton size="small" type="danger" link @click="removeField($index)"
                      >删除</ElButton
                    >
                  </template>
                </ElTableColumn>
              </ElTable>
            </div>
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
import CatalogAPI, { type CatColumn } from "@/api/module_semantic/catalog";
import EntityAPI, {
  type EntityForm,
  type EntityPageQuery,
  type EntityTable,
  type SemField,
} from "@/api/module_semantic/entity";
import DomainSelect from "../_shared/DomainSelect.vue";
import PhysicalTableSelect from "../_shared/PhysicalTableSelect.vue";
import PhysicalColumnSelect from "../_shared/PhysicalColumnSelect.vue";
import PhysicalColumnNamesSelect from "../_shared/PhysicalColumnNamesSelect.vue";
import { buildEntityFieldsFromColumns, mergeMissingFieldsFromColumns } from "./form";

defineOptions({ name: "SemanticEntity", inheritAttrs: false });

const { hasAuth } = useAuth();
const route = useRoute();
// 跨页跳转高亮:其他语义页通过 goEntity 带 ?focus=<id> 跳转到本页,
// 命中该 id 的行加高亮样式 class,并提示用户。
const focusId = ref<number | null>(null);
function rowClassName({ row }: { row: Record<string, any>; rowIndex: number }) {
  return focusId.value != null && (row as EntityTable).id === focusId.value ? "fa-focus-row" : "";
}
const STATUS_MAP: Record<number, { type: string; text: string }> = {
  0: { type: "info", text: "草稿" },
  1: { type: "warning", text: "待审" },
  2: { type: "success", text: "已发布" },
  3: { type: "danger", text: "已归档" },
};
const TYPE_MAP: Record<string, string> = {
  fact: "事实",
  dimension: "维表",
  detail: "明细",
  summary: "汇总",
};

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchForm = ref<Record<string, unknown>>({});
const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "实体编码",
    key: "code",
    type: "input",
    span: 6,
    props: { clearable: true, placeholder: "请输入编码" },
  },
  {
    label: "实体名称",
    key: "name",
    type: "input",
    span: 6,
    props: { clearable: true, placeholder: "请输入名称" },
  },
  {
    label: "类型",
    key: "entity_type",
    type: "select",
    span: 6,
    props: {
      clearable: true,
      placeholder: "请选择类型",
      options: [
        { label: "事实", value: "fact" },
        { label: "维表", value: "dimension" },
        { label: "明细", value: "detail" },
        { label: "汇总", value: "summary" },
      ],
    },
  },
  {
    label: "状态",
    key: "status",
    type: "select",
    span: 6,
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
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<EntityTable>();
const createLoading = ref(false);

const { dialogVisible } = useCrudDialog();
const detailFormData = ref<EntityTable>({} as EntityTable);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);
const physicalColumns = ref<CatColumn[]>([]);

const initialFormData: EntityForm = {
  id: undefined,
  code: "",
  name: "",
  entity_type: "fact",
  description: undefined,
  domain_code: undefined,
  physical_table_id: undefined,
  physical_datasource: undefined,
  physical_database: undefined,
  physical_table: undefined,
  grain: undefined,
  default_time_field: undefined,
  fields: [],
};
const formData = ref<EntityForm>({ ...initialFormData, fields: [] });

const rules = reactive({
  code: [{ required: true, message: "请输入实体编码", trigger: "blur" }],
  name: [{ required: true, message: "请输入实体名称", trigger: "blur" }],
  entity_type: [{ required: true, message: "请选择实体类型", trigger: "change" }],
});

const detailItems: import("@/components/others/fa-descriptions/index.vue").DescriptionsItem[] = [
  { label: "实体编码", prop: "code" },
  { label: "实体名称", prop: "name" },
  { label: "类型", prop: "entity_type" },
  { label: "业务域", prop: "domain_code" },
  { label: "物理库", prop: "physical_database" },
  { label: "物理表", prop: "physical_table" },
  { label: "粒度", prop: "grain" },
  { label: "默认时间字段", prop: "default_time_field" },
  {
    label: "状态",
    prop: "status",
    tag: {
      map: {
        "0": { type: "info", text: "草稿" },
        "1": { type: "warning", text: "待审" },
        "2": { type: "success", text: "已发布" },
        "3": { type: "danger", text: "已归档" },
      },
    },
  },
  { label: "描述", prop: "description", span: 2 },
  { label: "字段", prop: "fields", slot: "fields", span: 2 },
];

const dialogFormItems = computed<FormItem[]>(() => [
  {
    label: "实体编码",
    key: "code",
    type: "input",
    span: 12,
    props: { placeholder: "如：order_wide", maxlength: 50 },
  },
  {
    label: "实体名称",
    key: "name",
    type: "input",
    span: 12,
    props: { placeholder: "如：订单宽表", maxlength: 100 },
  },
  {
    label: "类型",
    key: "entity_type",
    type: "select",
    span: 12,
    props: {
      options: [
        { label: "事实", value: "fact" },
        { label: "维表", value: "dimension" },
        { label: "明细", value: "detail" },
        { label: "汇总", value: "summary" },
      ],
    },
  },
  { label: "业务域", key: "domain_code", span: 12 },
  { label: "物理表", key: "physical_table_id", span: 24 },
  { label: "粒度", key: "grain", span: 12 },
  { label: "默认时间字段", key: "default_time_field", span: 12 },
  {
    label: "描述",
    key: "description",
    type: "input",
    span: 24,
    props: { type: "textarea", rows: 2 },
  },
  { label: "字段", key: "fields", type: "input", span: 24 },
]);

function addField() {
  if (!formData.value.fields) formData.value.fields = [];
  formData.value.fields.push({
    code: "",
    name: "",
    field_role: "dimension",
    visibility: "public",
    physical_column_id: undefined,
  } as SemField);
}
function removeField(index: number) {
  formData.value.fields?.splice(index, 1);
}
/** 选物理列后回填:仅补空,不清已有手工值;清空物理列只回滚列名本身 */
function applyFieldColumnMeta(
  row: SemField,
  m: { column?: string; colType?: string; comment?: string }
) {
  row.physical_column = m.column;
  if (!m.column) return;
  if (!row.code) row.code = m.column;
  if (!row.name) row.name = m.comment || m.column;
  if (!row.data_type) row.data_type = m.colType;
}

const syncFieldsLoading = ref(false);
/** 编辑态:拉物理表列,仅追加尚未登记的字段,已改过的字段原样保留 */
async function syncFieldsFromTable() {
  const tableId = formData.value.physical_table_id;
  if (!tableId) return;
  syncFieldsLoading.value = true;
  try {
    const res = await CatalogAPI.tableDetail(tableId);
    const cols = res.data.data?.columns ?? [];
    // 以点击时刻的 fields 快照合并后整体替换;并发窗口仅限本地编辑,可接受
    const { fields, added } = mergeMissingFieldsFromColumns(formData.value.fields ?? [], cols);
    formData.value.fields = fields;
    if (added > 0) ElMessage.success(`已追加 ${added} 个新物理列对应字段`);
    else ElMessage.info("字段已与物理列一致");
  } catch {
    ElMessage.error("加载物理列失败");
  } finally {
    syncFieldsLoading.value = false;
  }
}
function handlePhysicalTableChange(tableId: number | undefined) {
  formData.value.physical_table_id = tableId;
  physicalColumns.value = [];
  if (!tableId) {
    formData.value.physical_datasource = undefined;
    formData.value.physical_database = undefined;
    formData.value.physical_table = undefined;
  }
  if (dialogVisible.type === "create") {
    formData.value.fields = [];
  }
}
function handlePhysicalTableMeta(meta: { datasource?: string; database?: string; table?: string }) {
  formData.value.physical_datasource = meta.datasource;
  formData.value.physical_database = meta.database;
  formData.value.physical_table = meta.table;
}
function handlePhysicalTableColumns(columns: CatColumn[]) {
  physicalColumns.value = columns;
  if (dialogVisible.type === "create" && formData.value.physical_table_id) {
    formData.value.fields = buildEntityFieldsFromColumns(columns);
  }
}

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<EntityForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    detailApi: EntityAPI.detailEntity,
    createApi: EntityAPI.createEntity,
    updateApi: EntityAPI.updateEntity,
    titles: { create: "新增实体", update: "修改实体", detail: "实体详情" },
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
    physicalColumns.value = [];
    await handleOpenDialog("create");
    formData.value.fields = [];
  } finally {
    createLoading.value = false;
  }
}

watch(
  () => dialogVisible.visible,
  (visible) => {
    if (!visible) physicalColumns.value = [];
  }
);

async function deleteRow(id: number) {
  try {
    await confirmDelete();
    await EntityAPI.deleteEntity([id]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    /* 取消 */
  }
}

async function changeStatus(row: EntityTable, target: number) {
  try {
    await ElMessageBox.confirm(
      `确认将「${row.name}」置为「${(STATUS_MAP[target] ?? STATUS_MAP[0]!).text}」?`,
      "状态流转",
      { type: "warning" }
    );
    await EntityAPI.changeStatus(row.id!, target);
    ElMessage.success("状态已更新");
    await refreshData();
  } catch (e) {
    if (e !== "cancel") ElMessage.error("状态流转失败");
  }
}

function buildRowActions(row: EntityTable): TableOperationAction[] {
  const s = row.status;
  const all: TableOperationAction[] = [
    {
      key: "detail",
      label: "详情",
      artType: "view",
      perm: "module_semantic:entity:detail",
      run: () => void handleOpenDialog("detail", row.id!),
    },
    {
      key: "edit",
      label: "编辑",
      artType: "edit",
      perm: "module_semantic:entity:update",
      run: () => void handleOpenDialog("update", row.id!),
    },
  ];
  if (s === 0)
    all.push({
      key: "submit",
      label: "提审",
      artType: "view",
      perm: "module_semantic:entity:status",
      run: () => changeStatus(row, 1),
    });
  if (s === 1)
    all.push({
      key: "publish",
      label: "发布",
      artType: "view",
      perm: "module_semantic:entity:status",
      run: () => changeStatus(row, 2),
    });
  if (s === 2)
    all.push({
      key: "withdraw",
      label: "撤回",
      artType: "view",
      perm: "module_semantic:entity:status",
      run: () => changeStatus(row, 1),
    });
  all.push({
    key: "delete",
    label: "删除",
    artType: "delete",
    perm: "module_semantic:entity:delete",
    run: () => deleteRow(row.id!),
  });
  return all.filter((a) => a.perm != null && hasAuth(a.perm));
}

const {
  columns,
  columnChecks,
  data,
  loading,
  pagination,
  getData,
  replaceSearchParams,
  resetSearchParams,
  handleSizeChange,
  handleCurrentChange,
  refreshData,
  refreshCreate,
  refreshUpdate,
  refreshRemove,
} = useTable({
  core: {
    apiFn: EntityAPI.listEntity,
    apiParams: { page_no: 1, page_size: 10 } as EntityPageQuery,
    columnsFactory: () => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "code", label: "编码", minWidth: 120, showOverflowTooltip: true },
      { prop: "name", label: "名称", minWidth: 140, showOverflowTooltip: true },
      {
        prop: "entity_type",
        label: "类型",
        width: 80,
        formatter: (row: EntityTable) => TYPE_MAP[row.entity_type] || row.entity_type,
      },
      { prop: "domain_code", label: "业务域", minWidth: 100, showOverflowTooltip: true },
      { prop: "physical_table", label: "物理表", minWidth: 140, showOverflowTooltip: true },
      {
        prop: "fields",
        label: "字段数",
        width: 70,
        align: "center",
        formatter: (row: EntityTable) => row.fields?.length ?? 0,
      },
      {
        prop: "status",
        label: "状态",
        width: 90,
        formatter: (row: EntityTable) => {
          const m = STATUS_MAP[row.status] ?? STATUS_MAP[0]!;
          return h(
            "span",
            { class: `el-tag el-tag--${m.type} el-tag--small`, style: "padding:0 8px" },
            m.text
          );
        },
      },
      { prop: "updated_time", label: "更新时间", width: 168, showOverflowTooltip: true },
      {
        prop: "operation",
        label: "操作",
        width: 240,
        fixed: "right",
        align: "center",
        formatter: (row: EntityTable) =>
          renderTableOperationCell(buildRowActions(row), {
            wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
          }),
      },
    ],
  },
});

onMounted(() => {
  const raw = route.query.focus;
  const id = Number(Array.isArray(raw) ? raw[0] : raw);
  if (raw != null && !Number.isNaN(id)) {
    focusId.value = id;
    ElMessage.info(`已定位到实体 #${id},在列表中高亮显示`);
  }
});

async function handleSearchBarSearch(params: Record<string, unknown>) {
  replaceSearchParams({
    code: params.code as string | undefined,
    name: params.name as string | undefined,
    entity_type: params.entity_type as string | undefined,
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
    await EntityAPI.deleteEntity(ids);
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

.field-editor {
  width: 100%;

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
    font-weight: 500;
  }

  &__actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}
</style>
