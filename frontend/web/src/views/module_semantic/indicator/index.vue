<!-- 指标管理：语义中心 - 指标(含多口径) + 生命周期状态机 -->
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
            :perm-create="['module_semantic:indicator:create']"
            :perm-delete="['module_semantic:indicator:delete']"
            :delete-loading="batchDeleting"
            :create-loading="createLoading"
            @add="handleAdd"
            @delete="handleBatchDelete"
          />
          <!-- 跨域不可算指标没有主实体 → 模型树与快照都不加载它们，列表页也搜不到。
               不给独立入口就等于录了一批看不见的数据 -->
          <ElButton
            v-if="hasAuth('module_semantic:indicator:list')"
            size="small" class="ml-2" @click="openCrossDomain"
          >
            跨域不可算指标
          </ElButton>
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
      width="820px"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @confirm="dialogVisible.type === 'detail' ? handleCloseDialog() : handleSubmit()"
    >
      <template v-if="dialogVisible.type === 'detail'">
        <FaDescriptions :column="2" :data="detailFormData" :items="detailItems" max-height="72vh">
          <template #primary_entity_id="{ value }">
            <ElButton v-if="value != null" type="primary" link @click="goEntity(Number(value))">
              实体 #{{ value }}
            </ElButton>
            <span v-else>-</span>
          </template>
          <template #impls>
            <ElTable :data="detailFormData.impls || []" size="small" border>
              <ElTableColumn prop="role" label="角色" width="80">
                <template #default="{ row }">{{ row.role === "primary" ? "主" : "副" }}</template>
              </ElTableColumn>
              <ElTableColumn prop="data_layer" label="数据层" width="80" />
              <ElTableColumn prop="sql_text" label="SQL" show-overflow-tooltip />
              <ElTableColumn label="审核" width="80">
                <template #default="{ row }">
                  <ElTag :type="implReviewTag(row.review_status).type as any" size="small">
                    {{ implReviewTag(row.review_status).text }}
                  </ElTag>
                </template>
              </ElTableColumn>
              <ElTableColumn v-if="canReview" label="操作" width="120" align="center">
                <template #default="{ row }">
                  <ElButton size="small" type="success" link @click="reviewImpl(row, 1)">通过</ElButton>
                  <ElButton size="small" type="danger" link @click="reviewImpl(row, 2)">驳回</ElButton>
                </template>
              </ElTableColumn>
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
          <template #primary_entity_id>
            <EntitySelect v-model="formData.primary_entity_id" />
          </template>
          <template #available_dimension_ids>
            <DimensionMultiSelect v-model="formData.available_dimension_ids" />
          </template>
          <template #domain>
            <DomainSelect v-model="formData.domain" />
          </template>
          <template #aliases>
            <AliasTagsSelect v-model="formData.aliases" />
          </template>
          <template #measure_expression>
            <div class="expr-line">
              <ElInput
                ref="measureInputRef"
                :model-value="formData.measure_expression"
                type="textarea"
                :rows="2"
                placeholder="原子指标的度量表达式,可用右侧「插入列」引用主实体物理列"
                @update:model-value="(v: string) => (formData.measure_expression = v)"
              />
              <ColumnInsertSelect
                v-if="primaryColumns.length"
                :columns="primaryColumns"
                :get-target="() => measureInputRef"
                @insert="(v: string) => (formData.measure_expression = v)"
              />
            </div>
          </template>
          <template #impls>
            <div class="impl-editor">
              <div class="impl-editor__head">
                <span>口径列表</span>
                <ElButton size="small" type="primary" plain @click="addImpl">新增口径</ElButton>
              </div>
              <ElTable :data="formData.impls || []" size="small" border>
                <ElTableColumn label="角色" width="120">
                  <template #default="{ row }">
                    <ElSelect v-model="row.role" size="small">
                      <ElOption label="主口径" value="primary" />
                      <ElOption label="副口径" value="secondary" />
                    </ElSelect>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="数据层" width="130">
                  <template #default="{ row }">
                    <ElSelect
                      v-model="row.data_layer"
                      size="small"
                      filterable
                      allow-create
                      default-first-option
                      clearable
                      placeholder="ADS/DWS"
                    >
                      <ElOption v-for="opt in DATA_LAYER_OPTIONS" :key="opt" :label="opt" :value="opt" />
                    </ElSelect>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="SQL" min-width="280">
                  <template #default="{ row, $index }">
                    <div class="expr-line">
                      <ElInput
                        :ref="(el) => setSqlRef($index, el)"
                        v-model="row.sql_text"
                        type="textarea"
                        :rows="2"
                        size="small"
                        placeholder="口径 SQL"
                      />
                      <ColumnInsertSelect
                        v-if="primaryColumns.length"
                        :columns="primaryColumns"
                        :get-target="() => sqlInputRefs.get($index) ?? null"
                        @insert="(v: string) => (row.sql_text = v)"
                      />
                    </div>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="标签" width="120">
                  <template #default="{ row }">
                    <ElInput v-model="row.label" size="small" />
                  </template>
                </ElTableColumn>
                <ElTableColumn label="操作" width="70" align="center">
                  <template #default="{ $index }">
                    <ElButton size="small" type="danger" link @click="removeImpl($index)">删除</ElButton>
                  </template>
                </ElTableColumn>
              </ElTable>
            </div>
          </template>
        </FaForm>
      </template>
    </FaDialog>

    <!-- 跨域不可算指标：录进来是为了让 agent 能做边界拒答，人也得看得到这份声明 -->
    <ElDrawer v-model="crossVisible" title="跨域不可算指标" size="52%">
      <ElAlert type="info" :closable="false" class="mb-3">
        <template #title>这些指标「不可从本模型独立计算」</template>
        <div class="text-xs leading-relaxed">
          它们没有主实体（如 ARPU 的分母是活跃用户，而 bill 模型没有活跃数据），
          所以<strong>模型树与发布快照都不含它们</strong>，普通列表页也搜不到。
          <br>
          录它们的价值在<strong>边界拒答</strong>：agent 命中后会回答"本模型不可算 +
          正确出处"，而不是编造或硬 JOIN 出一个看似合理的数。
        </div>
      </ElAlert>

      <ElTable v-loading="crossLoading" :data="crossList" size="small" border>
        <ElTableColumn prop="code" label="编码" min-width="140" show-overflow-tooltip />
        <ElTableColumn prop="name" label="名称" min-width="120" show-overflow-tooltip />
        <ElTableColumn label="类型" width="90">
          <template #default="{ row }">
            {{ TYPE_MAP[row.indicator_type] || row.indicator_type }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="状态" width="86" align="center">
          <template #default="{ row }">
            <ElTag :type="(LIFECYCLE_MAP[row.lifecycle_status]?.type as any) || 'info'" size="small">
              {{ LIFECYCLE_MAP[row.lifecycle_status]?.text || row.lifecycle_status }}
            </ElTag>
          </template>
        </ElTableColumn>
        <!-- 出处是这类声明的核心内容：没有它 agent 只能说"算不了"，帮不上用户 -->
        <ElTableColumn prop="business_definition" label="不可算说明 / 出处" min-width="220">
          <template #default="{ row }">
            <span v-if="row.business_definition">{{ row.business_definition }}</span>
            <ElTooltip v-else content="缺出处时 agent 只能说「算不了」，无法告诉用户该去哪查" placement="top">
              <ElTag type="warning" size="small">未写出处</ElTag>
            </ElTooltip>
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="80" align="center">
          <template #default="{ row }">
            <ElButton size="small" link type="primary" @click="openCrossDetail(row.id)">
              详情
            </ElButton>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty :image-size="60" description="没有跨域不可算指标" />
        </template>
      </ElTable>
    </ElDrawer>
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElMessage, ElMessageBox, type InputInstance } from "element-plus";
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
import IndicatorAPI, {
  type IndicatorForm,
  type IndicatorImpl,
  type IndicatorPageQuery,
  type IndicatorTable,
} from "@/api/module_semantic/indicator";
import EntityAPI from "@/api/module_semantic/entity";
import CatalogAPI, { type CatColumn } from "@/api/module_semantic/catalog";
import EntitySelect from "../_shared/EntitySelect.vue";
import DimensionMultiSelect from "../_shared/DimensionMultiSelect.vue";
import DomainSelect from "../_shared/DomainSelect.vue";
import AliasTagsSelect from "../_shared/AliasTagsSelect.vue";
import ColumnInsertSelect from "../_shared/ColumnInsertSelect.vue";
import { useNavigateToNode } from "../_shared/useNavigateToNode";

const DATA_LAYER_OPTIONS = ["ODS", "DWD", "DWS", "ADS"];
const UNIT_OPTIONS = ["元", "万元", "亿元", "%", "次", "个", "人", "天"];

defineOptions({ name: "SemanticIndicator", inheritAttrs: false });

const { goEntity } = useNavigateToNode();

const { hasAuth } = useAuth();
const canReview = computed(() => hasAuth("module_semantic:indicator:review"));
const route = useRoute();
// 跨页跳转高亮:其他语义页通过带 ?focus=<id> 跳转到本页,
// 命中该 id 的行加高亮样式 class,并提示用户。
const focusId = ref<number | null>(null);
function rowClassName({ row }: { row: Record<string, any>; rowIndex: number }) {
  return focusId.value != null && (row as IndicatorTable).id === focusId.value ? "fa-focus-row" : "";
}

const LIFECYCLE_MAP: Record<number, { type: string; text: string }> = {
  0: { type: "info", text: "草稿" },
  1: { type: "warning", text: "待审" },
  2: { type: "success", text: "已发布" },
  3: { type: "danger", text: "已归档" },
};
const TYPE_MAP: Record<string, string> = {
  atomic: "原子",
  derived: "派生",
  composite: "复合",
};
function implReviewTag(s?: number) {
  return { 0: { type: "info", text: "待审" }, 1: { type: "success", text: "通过" }, 2: { type: "danger", text: "驳回" } }[
    s ?? 0
  ]!;
}

// ─── 搜索 ───
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchForm = ref<Record<string, unknown>>({});
const searchItems = computed<SearchFormItem[]>(() => [
  { label: "指标编码", key: "code", type: "input", span: 6, props: { clearable: true, placeholder: "请输入编码" } },
  { label: "指标名称", key: "name", type: "input", span: 6, props: { clearable: true, placeholder: "请输入名称" } },
  {
    label: "类型",
    key: "indicator_type",
    type: "select",
    span: 6,
    props: {
      clearable: true,
      placeholder: "请选择类型",
      options: [
        { label: "原子", value: "atomic" },
        { label: "派生", value: "derived" },
        { label: "复合", value: "composite" },
      ],
    },
  },
  {
    label: "状态",
    key: "lifecycle_status",
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

// ─── 多选 ───
const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<IndicatorTable>();
const createLoading = ref(false);

// ─── 对话框 / 表单 ───
const { dialogVisible } = useCrudDialog();
const detailFormData = ref<IndicatorTable>({} as IndicatorTable);
const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

const initialFormData: IndicatorForm = {
  id: undefined,
  code: "",
  name: "",
  indicator_type: "atomic",
  decimal_places: 2,
  description: undefined,
  business_definition: undefined,
  domain: undefined,
  business_process: undefined,
  measure_expression: undefined,
  unit: undefined,
  business_importance: undefined,
  primary_entity_id: undefined,
  available_dimension_ids: undefined,
  // 显式 undefined:useCrudForm 用 Object.assign 重置,缺键会残留上一次编辑的别名。
  // JSON 序列化时 undefined 键自动消失,create payload 与改造前一致。
  aliases: undefined,
  impls: [],
};
const formData = ref<IndicatorForm>({ ...initialFormData, impls: [] });

// ─── 主实体物理列联动(度量表达式/口径 SQL 的「插入列」辅助) ───
const primaryColumns = ref<CatColumn[]>([]);
let primaryColumnsToken = 0;
watch(
  () => formData.value.primary_entity_id,
  async (id) => {
    const token = ++primaryColumnsToken;
    primaryColumns.value = [];
    if (!id) return;
    try {
      const entRes = await EntityAPI.detailEntity(id);
      if (token !== primaryColumnsToken) return;
      const tableId = entRes.data.data?.physical_table_id;
      if (!tableId) return; // 实体未绑物理表 → 无列可辅助
      const catRes = await CatalogAPI.tableDetail(tableId);
      if (token !== primaryColumnsToken) return;
      primaryColumns.value = catRes.data.data?.columns ?? [];
    } catch (e) {
      console.warn("[indicator] 加载主实体物理列失败,降级无辅助", e);
      if (token === primaryColumnsToken) primaryColumns.value = [];
    }
  }
);
// 弹窗关闭清空派生状态(token 自增同时作废在途请求)
watch(
  () => dialogVisible.visible,
  (v) => {
    if (!v) {
      primaryColumnsToken++;
      primaryColumns.value = [];
    }
  }
);

const measureInputRef = ref<InputInstance | null>(null);
const sqlInputRefs = new Map<number, InputInstance>();
function setSqlRef(index: number, el: unknown) {
  if (el == null) sqlInputRefs.delete(index);
  else sqlInputRefs.set(index, el as InputInstance);
}

const rules = reactive({
  code: [{ required: true, message: "请输入指标编码", trigger: "blur" }],
  name: [{ required: true, message: "请输入指标名称", trigger: "blur" }],
  indicator_type: [{ required: true, message: "请选择指标类型", trigger: "change" }],
});

const detailItems: import("@/components/others/fa-descriptions/index.vue").DescriptionsItem[] = [
  { label: "指标编码", prop: "code" },
  { label: "指标名称", prop: "name" },
  { label: "类型", prop: "indicator_type" },
  { label: "业务域", prop: "domain" },
  { label: "业务过程", prop: "business_process" },
  { label: "单位", prop: "unit" },
  {
    label: "状态",
    prop: "lifecycle_status",
    tag: { map: { "0": { type: "info", text: "草稿" }, "1": { type: "warning", text: "待审" }, "2": { type: "success", text: "已发布" }, "3": { type: "danger", text: "已归档" } } },
  },
  { label: "业务重要度", prop: "business_importance" },
  { label: "主实体", prop: "primary_entity_id", slot: "primary_entity_id" },
  { label: "度量表达式", prop: "measure_expression", span: 2 },
  { label: "业务定义", prop: "business_definition", span: 2 },
  { label: "描述", prop: "description", span: 2 },
  { label: "口径", prop: "impls", slot: "impls", span: 2 },
];

const dialogFormItems = computed<FormItem[]>(() => [
  { label: "指标编码", key: "code", type: "input", span: 12, props: { placeholder: "字母/数字编码", maxlength: 50 } },
  { label: "指标名称", key: "name", type: "input", span: 12, props: { placeholder: "请输入名称", maxlength: 100 } },
  {
    label: "类型",
    key: "indicator_type",
    type: "select",
    span: 12,
    props: {
      options: [
        { label: "原子", value: "atomic" },
        { label: "派生", value: "derived" },
        { label: "复合", value: "composite" },
      ],
    },
  },
  { label: "业务域", key: "domain", span: 12 },
  { label: "别名", key: "aliases", span: 12 },
  { label: "业务过程", key: "business_process", type: "input", span: 12, props: { placeholder: "如：下单" } },
  {
    label: "单位",
    key: "unit",
    type: "select",
    span: 12,
    props: {
      placeholder: "如：元",
      clearable: true,
      filterable: true,
      allowCreate: true,
      defaultFirstOption: true,
      options: UNIT_OPTIONS.map((u) => ({ label: u, value: u })),
    },
  },
  {
    label: "业务重要度",
    key: "business_importance",
    type: "number",
    span: 12,
    props: { min: 1, max: 5, controlsPosition: "right", style: { width: "120px" } },
  },
  { label: "度量表达式", key: "measure_expression", span: 24 },
  { label: "业务定义", key: "business_definition", type: "input", span: 24, props: { type: "textarea", rows: 2 } },
  { label: "描述", key: "description", type: "input", span: 24, props: { type: "textarea", rows: 2 } },
  { label: "主实体", key: "primary_entity_id", span: 12 },
  { label: "可用维度", key: "available_dimension_ids", span: 12 },
  { label: "口径", key: "impls", type: "input", span: 24 },
]);

function addImpl() {
  if (!formData.value.impls) formData.value.impls = [];
  formData.value.impls.push({ role: "secondary", sql_text: "", label: "", data_layer: "" } as IndicatorImpl);
}
function removeImpl(index: number) {
  formData.value.impls?.splice(index, 1);
}

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } = useCrudForm<IndicatorForm>({
  formData,
  initialFormData,
  dialogVisible,
  dataFormRef,
  formRenderKey,
  detailApi: IndicatorAPI.detailIndicator,
  createApi: IndicatorAPI.createIndicator,
  updateApi: IndicatorAPI.updateIndicator,
  titles: { create: "新增指标", update: "修改指标", detail: "指标详情" },
  detailFormData,
  onCreateSuccess: async () => {
    await refreshCreate();
  },
  onUpdateSuccess: async () => {
    await refreshUpdate();
  },
});

// ---- 跨域不可算指标 ----
//
// 这类指标 primary_entity_id 为空 → 模型树按成员过滤时根本不加载它们，
// 发布快照也不含，普通列表页同样搜不到。不给独立入口就等于录了一批看不见的数据。
// 它们的价值在边界拒答：agent 命中后说"本模型不可算 + 正确出处"，而非编造。

const crossVisible = ref(false);
const crossLoading = ref(false);
const crossList = ref<IndicatorTable[]>([]);

async function loadCrossDomain() {
  crossLoading.value = true;
  try {
    const res = await IndicatorAPI.crossDomain();
    crossList.value = res.data.data ?? [];
  } catch {
    crossList.value = []; // 读失败不该让整页崩
  } finally {
    crossLoading.value = false;
  }
}

function openCrossDomain() {
  crossVisible.value = true;
  void loadCrossDomain();
}

/** 看详情前先收起抽屉：详情弹窗与抽屉叠在一起会互相盖住 */
function openCrossDetail(id?: number) {
  if (id == null) return;
  crossVisible.value = false;
  void handleOpenDialog("detail", id);
}

async function handleAdd() {
  createLoading.value = true;
  try {
    await handleOpenDialog("create");
    formData.value.impls = [];
  } finally {
    createLoading.value = false;
  }
}

async function deleteRow(id: number) {
  try {
    await confirmDelete();
    await IndicatorAPI.deleteIndicator([id]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    /* 取消 */
  }
}

// 口径审核（详情抽屉内）
async function reviewImpl(row: IndicatorImpl, result: number) {
  if (!row.id) return;
  try {
    await IndicatorAPI.reviewImpl(row.id, result);
    row.review_status = result;
    ElMessage.success(result === 1 ? "已通过" : "已驳回");
    await refreshData();
  } catch {
    ElMessage.error("审核失败");
  }
}

// 状态流转
async function changeStatus(row: IndicatorTable, target: number) {
  try {
    await ElMessageBox.confirm(`确认将「${row.name}」置为「${(LIFECYCLE_MAP[target] ?? LIFECYCLE_MAP[0]!).text}」?`, "状态流转", {
      type: "warning",
    });
    await IndicatorAPI.changeStatus(row.id!, target);
    ElMessage.success("状态已更新");
    await refreshData();
  } catch (e) {
    if (e !== "cancel") ElMessage.error("状态流转失败");
  }
}

function buildRowActions(row: IndicatorTable): TableOperationAction[] {
  const s = row.lifecycle_status;
  const all: TableOperationAction[] = [
    { key: "detail", label: "详情", artType: "view", perm: "module_semantic:indicator:detail", run: () => void handleOpenDialog("detail", row.id!) },
    { key: "edit", label: "编辑", artType: "edit", perm: "module_semantic:indicator:update", run: () => void handleOpenDialog("update", row.id!) },
  ];
  // 状态流转动作
  if (s === 0) all.push({ key: "submit", label: "提审", artType: "view", perm: "module_semantic:indicator:status", run: () => changeStatus(row, 1) });
  if (s === 1) all.push({ key: "publish", label: "发布", artType: "view", perm: "module_semantic:indicator:status", run: () => changeStatus(row, 2) });
  if (s === 2) all.push({ key: "withdraw", label: "撤回", artType: "view", perm: "module_semantic:indicator:status", run: () => changeStatus(row, 1) });
  all.push({ key: "delete", label: "删除", artType: "delete", perm: "module_semantic:indicator:delete", run: () => deleteRow(row.id!) });
  return all.filter((a) => a.perm != null && hasAuth(a.perm));
}

const { columns, columnChecks, data, loading, pagination, getData, replaceSearchParams, resetSearchParams, handleSizeChange, handleCurrentChange, refreshData, refreshCreate, refreshUpdate, refreshRemove } = useTable({
  core: {
    apiFn: IndicatorAPI.listIndicator,
    apiParams: { page_no: 1, page_size: 10 } as IndicatorPageQuery,
    columnsFactory: () => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "code", label: "编码", minWidth: 120, showOverflowTooltip: true },
      { prop: "name", label: "名称", minWidth: 140, showOverflowTooltip: true },
      { prop: "indicator_type", label: "类型", width: 80, formatter: (row: IndicatorTable) => TYPE_MAP[row.indicator_type] || row.indicator_type },
      { prop: "domain", label: "业务域", minWidth: 100, showOverflowTooltip: true },
      { prop: "impls", label: "口径数", width: 70, align: "center", formatter: (row: IndicatorTable) => (row.impls?.length ?? 0) },
      { prop: "business_importance", label: "重要度", width: 70, align: "center" },
      {
        prop: "lifecycle_status",
        label: "状态",
        width: 90,
        formatter: (row: IndicatorTable) => {
          const m = LIFECYCLE_MAP[row.lifecycle_status] ?? LIFECYCLE_MAP[0]!;
          return h("span", { class: `el-tag el-tag--${m.type} el-tag--small`, style: "padding:0 8px" }, m.text);
        },
      },
      { prop: "updated_time", label: "更新时间", width: 168, showOverflowTooltip: true },
      {
        prop: "operation",
        label: "操作",
        width: 240,
        fixed: "right",
        align: "center",
        formatter: (row: IndicatorTable) =>
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
    ElMessage.info(`已定位到指标 #${id},在列表中高亮显示`);
  }
});

async function handleSearchBarSearch(params: Record<string, unknown>) {
  replaceSearchParams({
    code: params.code as string | undefined,
    name: params.name as string | undefined,
    indicator_type: params.indicator_type as string | undefined,
    lifecycle_status: params.lifecycle_status as number | undefined,
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
    await IndicatorAPI.deleteIndicator(ids);
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
.expr-line {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  :deep(.el-textarea),
  > :first-child {
    flex: 1;
    min-width: 0;
  }
}
.impl-editor {
  width: 100%;
  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
    font-weight: 500;
  }
}
</style>
