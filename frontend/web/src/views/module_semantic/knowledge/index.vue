<!-- 知识库：语义中心 - L6 知识条目(含使用说明) + 交叉引用 -->
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
      <FaTableHeader v-model:columns="columnChecks" v-model:showSearchBar="showSearchBar" :loading="loading" @refresh="refreshData">
        <template #left>
          <FaTableHeaderLeft
            :remove-ids="selectedIds"
            :perm-create="['module_semantic:knowledge:create']"
            :perm-delete="['module_semantic:knowledge:delete']"
            :delete-loading="batchDeleting"
            :create-loading="createLoading"
            @add="handleAdd"
            @delete="handleBatchDelete"
          />
          <!-- 治理入口：待复核的三个条件(从未复核/超周期/级联标记)都算不出来，
               列表页的 status 筛选给不出，故必须是独立入口 -->
          <ElButton
            v-if="hasAuth('module_semantic:knowledge:list')"
            size="small" class="ml-2" @click="openStale"
          >
            待复核清单
          </ElButton>
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
      width="900px"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @confirm="dialogVisible.type === 'detail' ? handleCloseDialog() : handleSubmit()"
    >
      <!-- 详情 -->
      <template v-if="dialogVisible.type === 'detail'">
        <ElDescriptions :column="2" border size="small" class="mb-3">
          <ElDescriptionsItem label="键">{{ detailData.key }}</ElDescriptionsItem>
          <ElDescriptionsItem label="类型">{{ TYPE_MAP[detailData.knowledge_type] || detailData.knowledge_type }}</ElDescriptionsItem>
          <ElDescriptionsItem label="使用模式">{{ detailData.usage_mode }}</ElDescriptionsItem>
          <ElDescriptionsItem label="置信度">{{ detailData.confidence ?? "-" }}</ElDescriptionsItem>
          <ElDescriptionsItem label="来源">
            <ElTag :type="(SOURCE_MAP[detailData.source]?.type as any) || 'info'" size="small">
              {{ SOURCE_MAP[detailData.source]?.text || detailData.source || "-" }}
            </ElTag>
          </ElDescriptionsItem>
          <ElDescriptionsItem label="上次复核">
            <span v-if="detailData.reviewed_at">{{ detailData.reviewed_at }}</span>
            <ElTag v-else type="warning" size="small">从未复核</ElTag>
            <ElTag v-if="detailData.needs_review" type="danger" size="small" class="ml-2">待复核</ElTag>
          </ElDescriptionsItem>
          <ElDescriptionsItem label="摘要" :span="2">{{ detailData.summary }}</ElDescriptionsItem>
          <!-- 把守卫说清：否则"点发布报错"在详情页找不到任何解释 -->
          <ElDescriptionsItem v-if="blockedByReviewGuard(detailData)" label="发布限制" :span="2">
            <span class="text-orange-500">
              LLM 生成的知识不自动获得权威性，需先「标记已复核」才能发布
            </span>
          </ElDescriptionsItem>
          <ElDescriptionsItem v-if="detailData.caliber_break_date" label="口径分水岭" :span="2">
            <ElTag type="warning" size="small">{{ detailData.caliber_break_date }}</ElTag>
            <span class="ml-2 text-xs">次日起为新口径；跨越它的查询会被拦下要求声明口径</span>
            <div v-if="detailData.caliber_break_note" class="kb-hint">{{ detailData.caliber_break_note }}</div>
          </ElDescriptionsItem>
        </ElDescriptions>
        <div class="font-medium mb-1">正文</div>
        <FaMarkdownRenderer :content="detailData.body || ''" class="kb-preview mb-3" />
        <template v-if="detailData.representative_sql">
          <div class="font-medium mb-1">示例 SQL</div>
          <pre class="kb-sql mb-3">{{ detailData.representative_sql }}</pre>
        </template>
        <div class="font-medium mb-1">交叉引用（{{ detailData.refs?.length ?? 0 }}）</div>
        <ElTable :data="detailData.refs || []" size="small" border>
          <ElTableColumn prop="target_type" label="目标类型" width="110" />
          <ElTableColumn prop="target_id" label="目标ID" width="90" />
          <ElTableColumn prop="target_label" label="目标名" show-overflow-tooltip />
          <ElTableColumn prop="ref_type" label="引用类型" width="110" />
        </ElTable>
      </template>

      <!-- 编辑/新增 -->
      <template v-else>
        <ElForm ref="formEl" :model="formData" :rules="rules" label-width="90px">
          <ElRow :gutter="16">
            <ElCol :span="12"><ElFormItem label="键" prop="key"><ElInput v-model="formData.key" placeholder="如 gmv-refund-policy" /></ElFormItem></ElCol>
            <ElCol :span="12">
              <ElFormItem label="类型" prop="knowledge_type">
                <ElSelect v-model="formData.knowledge_type" class="w-full">
                  <ElOption label="背景解释" value="background" />
                  <ElOption label="使用说明" value="usage" />
                  <ElOption label="避坑" value="caveat" />
                  <ElOption label="查询范式" value="query_pattern" />
                  <ElOption label="术语" value="glossary" />
                </ElSelect>
              </ElFormItem>
            </ElCol>
          </ElRow>
          <ElFormItem label="摘要" prop="summary"><ElInput v-model="formData.summary" maxlength="200" placeholder="一句话摘要(进Agent索引)" /></ElFormItem>
          <ElRow :gutter="16">
            <ElCol :span="12">
              <ElFormItem label="使用模式">
                <ElSelect v-model="formData.usage_mode" class="w-full">
                  <ElOption label="必用 (always)" value="always" />
                  <ElOption label="可用 (auto)" value="auto" />
                  <ElOption label="禁用 (never)" value="never" />
                </ElSelect>
              </ElFormItem>
            </ElCol>
            <ElCol :span="12"><ElFormItem label="置信度"><ElInputNumber v-model="formData.confidence" :min="1" :max="5" controls-position="right" /></ElFormItem></ElCol>
          </ElRow>
          <!-- 口径分水岭：驱动校验器 caliber_break 规则（跨越该日的查询会被拦下并要求声明口径）。
               此前靠在正文里正则搜"分水岭"+日期识别，改一句措辞就会让护栏静默失效 -->
          <ElRow :gutter="16">
            <ElCol :span="12">
              <ElFormItem label="口径分水岭">
                <ElDatePicker
                  v-model="formData.caliber_break_date"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="留空则不启用该护栏"
                  class="w-full"
                />
                <!-- ★ 语义不直观，必须在 UI 上写清：填错一天会让整个分界后移 -->
                <div class="kb-hint">
                  填<strong>最后一个旧口径日</strong>，新口径从次日起生效。
                  例：新口径 6/30 生效则填 6/29
                </div>
              </ElFormItem>
            </ElCol>
            <ElCol :span="12">
              <ElFormItem label="口径差异说明">
                <ElInput
                  v-model="formData.caliber_break_note"
                  maxlength="200"
                  placeholder="如：历史为名义价，直接汇总会显著虚高"
                />
                <div class="kb-hint">会进拒答消息给 agent，留空则用通用措辞</div>
              </ElFormItem>
            </ElCol>
          </ElRow>
          <ElFormItem label="正文">
            <div class="kb-editor">
              <div class="kb-editor__toolbar">
                <ElRadioGroup v-model="editorTab" size="small">
                  <ElRadioButton value="edit">编辑</ElRadioButton>
                  <ElRadioButton value="preview">预览</ElRadioButton>
                </ElRadioGroup>
              </div>
              <ElInput v-if="editorTab === 'edit'" v-model="formData.body" type="textarea" :rows="10" placeholder="Markdown 正文..." />
              <FaMarkdownRenderer v-else :content="formData.body || ''" class="kb-preview" />
            </div>
          </ElFormItem>
          <ElFormItem label="示例SQL"><ElInput v-model="formData.representative_sql" type="textarea" :rows="3" placeholder="推荐/示例 SQL(使用说明类常用)" /></ElFormItem>

          <ElFormItem label="交叉引用">
            <div class="kb-refs">
              <div class="kb-refs__head">
                <span>关联到语义实体</span>
                <ElButton size="small" type="primary" plain @click="addRef">新增引用</ElButton>
              </div>
              <ElTable :data="formData.refs || []" size="small" border>
                <ElTableColumn label="目标类型" width="130">
                  <template #default="{ row }">
                    <ElSelect
                      v-model="row.target_type"
                      size="small"
                      @change="() => { row.target_id = undefined; row.target_label = undefined; }"
                    >
                      <ElOption label="实体" value="entity" />
                      <ElOption label="字段" value="field" />
                      <ElOption label="维度" value="dimension" />
                      <ElOption label="指标" value="indicator" />
                      <ElOption label="JOIN" value="join" />
                      <ElOption label="知识" value="knowledge" />
                    </ElSelect>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="目标对象" width="200">
                  <template #default="{ row }">
                    <KnowledgeTargetSelect
                      :model-value="row.target_id"
                      :target-type="row.target_type"
                      @update:model-value="(v) => row.target_id = v"
                      @update:label="(l) => row.target_label = l"
                    />
                  </template>
                </ElTableColumn>
                <ElTableColumn label="目标名(可选)">
                  <template #default="{ row }"><ElInput v-model="row.target_label" size="small" /></template>
                </ElTableColumn>
                <ElTableColumn label="引用类型" width="130">
                  <template #default="{ row }">
                    <ElSelect v-model="row.ref_type" size="small">
                      <ElOption label="解释" value="describes" />
                      <ElOption label="使用说明" value="howto" />
                      <ElOption label="注意" value="caveats" />
                      <ElOption label="矛盾" value="contradicts" />
                    </ElSelect>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="操作" width="60" align="center">
                  <template #default="{ $index }"><ElButton size="small" type="danger" link @click="removeRef($index)">删除</ElButton></template>
                </ElTableColumn>
              </ElTable>
            </div>
          </ElFormItem>
        </ElForm>
      </template>
    </FaDialog>

    <!-- 待复核清单：知识会随业务漂移，"发布过"不等于"现在还对" -->
    <ElDrawer v-model="staleVisible" title="待复核知识" size="52%">
      <ElAlert type="info" :closable="false" class="mb-3">
        <template #title>三类知识会进本清单</template>
        <div class="text-xs leading-relaxed">
          ① 已发布但<strong>从未复核</strong>过 —— 否则它永远不进复核视野；
          ② 距上次复核<strong>超过复核周期</strong>；
          ③ 被<strong>标记待复核</strong>（引用目标失效扫描，或人工标记），哪怕刚复核过。
          <br>
          另：<strong>LLM 来源</strong>未复核的知识发布会被守卫拦下，需在此或列表页标记复核。
        </div>
      </ElAlert>

      <div class="mb-3 flex items-center gap-2">
        <ElButton
          v-if="hasAuth('module_semantic:knowledge:update')"
          size="small" :loading="scanning" @click="handleScanOrphan"
        >
          扫描悬空引用
        </ElButton>
        <ElButton size="small" :loading="staleLoading" @click="loadStale">刷新</ElButton>
        <span class="text-xs text-gray-400">扫描会把引用目标已失效的知识标记为待复核</span>
      </div>

      <ElTable v-loading="staleLoading" :data="staleList" size="small" border>
        <ElTableColumn prop="key" label="键" min-width="150" show-overflow-tooltip />
        <ElTableColumn prop="summary" label="摘要" min-width="180" show-overflow-tooltip />
        <ElTableColumn label="来源" width="76" align="center">
          <template #default="{ row }">
            <ElTag :type="(SOURCE_MAP[row.source]?.type as any) || 'info'" size="small">
              {{ SOURCE_MAP[row.source]?.text || row.source || "-" }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="上次复核" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.reviewed_at" class="text-xs">{{ row.reviewed_at.slice(0, 10) }}</span>
            <ElTag v-else type="warning" size="small">从未</ElTag>
          </template>
        </ElTableColumn>
        <!-- needs_review 不只由悬空引用扫描置起(也可由 update 人工标记)，
             所以这里只说"被标记"，不替后端断言原因 -->
        <ElTableColumn label="原因" width="100" align="center">
          <template #default="{ row }">
            <ElTag v-if="row.needs_review" type="danger" size="small">已标记</ElTag>
            <ElTag v-else-if="!row.reviewed_at" type="warning" size="small">从未复核</ElTag>
            <ElTag v-else type="info" size="small">超周期</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="110" align="center">
          <template #default="{ row }">
            <ElButton
              v-if="hasAuth('module_semantic:knowledge:update')"
              size="small" type="primary" link @click="markReviewed(row)"
            >
              标记已复核
            </ElButton>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty :image-size="60" description="没有待复核的知识" />
        </template>
      </ElTable>
    </ElDrawer>
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useTable } from "@/hooks/core/useTable";
import { useCrudDialog } from "@/hooks/core/useCrudDialog";
import { useTableSelection } from "@/hooks/core/useTableSelection";
import { confirmDelete, confirmBatchDelete } from "@/hooks/core/useConfirm";
import { renderTableOperationCell, type TableOperationAction } from "@utils";
import { useAuth } from "@/hooks/core/useAuth";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import KnowledgeTargetSelect from "../_shared/KnowledgeTargetSelect.vue";
import KnowledgeAPI, {
  type KnowledgeForm,
  type KnowledgePageQuery,
  type KnowledgeRef,
  type KnowledgeTable,
} from "@/api/module_semantic/knowledge";

defineOptions({ name: "SemanticKnowledge", inheritAttrs: false });

const { hasAuth } = useAuth();
const TYPE_MAP: Record<string, string> = {
  background: "背景解释",
  usage: "使用说明",
  caveat: "避坑",
  query_pattern: "查询范式",
  glossary: "术语",
};
const STATUS_MAP: Record<number, { type: string; text: string }> = {
  0: { type: "info", text: "草稿" },
  1: { type: "warning", text: "待审" },
  2: { type: "success", text: "已发布" },
  3: { type: "danger", text: "已归档" },
};
/** 来源。llm 单独标出来：它受源守卫约束，未复核不能发布 */
const SOURCE_MAP: Record<string, { type: string; text: string }> = {
  manual: { type: "info", text: "人工" },
  dbt: { type: "info", text: "dbt" },
  import: { type: "info", text: "导入" },
  llm: { type: "warning", text: "LLM" },
};
const SOURCE_LLM = "llm";

/** LLM 源且未复核 —— 后端 change_status 会拦它升「已发布」 */
function blockedByReviewGuard(row: KnowledgeTable): boolean {
  return row.source === SOURCE_LLM && !row.reviewed_at;
}

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchForm = ref<Record<string, unknown>>({});
const searchItems = computed<SearchFormItem[]>(() => [
  { label: "键", key: "key", type: "input", span: 6, props: { clearable: true, placeholder: "知识键" } },
  { label: "摘要", key: "summary", type: "input", span: 6, props: { clearable: true, placeholder: "摘要关键词" } },
  {
    label: "类型",
    key: "knowledge_type",
    type: "select",
    span: 6,
    props: {
      clearable: true,
      options: [
        { label: "背景解释", value: "background" },
        { label: "使用说明", value: "usage" },
        { label: "避坑", value: "caveat" },
        { label: "查询范式", value: "query_pattern" },
        { label: "术语", value: "glossary" },
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
const { selectedIds, batchDeleting, onTableSelectionChange } = useTableSelection<KnowledgeTable>();
const createLoading = ref(false);

const { dialogVisible } = useCrudDialog();
const detailData = ref<KnowledgeTable>({} as KnowledgeTable);
const formEl = ref();
const submitLoading = ref(false);
const editorTab = ref<"edit" | "preview">("edit");

const initialForm: KnowledgeForm = {
  id: undefined,
  key: "",
  summary: "",
  body: "",
  knowledge_type: "background",
  representative_sql: undefined,
  usage_mode: "auto",
  confidence: undefined,
  caliber_break_date: null,
  caliber_break_note: null,
  refs: [],
};
const formData = ref<KnowledgeForm>({ ...initialForm, refs: [] });

const rules = {
  key: [{ required: true, message: "请输入知识键", trigger: "blur" }],
  summary: [{ required: true, message: "请输入摘要", trigger: "blur" }],
  knowledge_type: [{ required: true, message: "请选择类型", trigger: "change" }],
};

function addRef() {
  if (!formData.value.refs) formData.value.refs = [];
  // 新增行不预置占位目标ID:target_id 保持 undefined,强制用户在 KnowledgeTargetSelect 中主动点选,避免误提交错误关联
  formData.value.refs.push({ target_type: "indicator", ref_type: "describes" } as KnowledgeRef);
}
function removeRef(index: number) {
  formData.value.refs?.splice(index, 1);
}

function handleAdd() {
  dialogVisible.type = "create";
  dialogVisible.title = "新增知识";
  formData.value = { ...initialForm, refs: [] };
  editorTab.value = "edit";
  dialogVisible.visible = true;
}
async function handleEdit(id: number) {
  const res = await KnowledgeAPI.detailKnowledge(id);
  const d = res.data.data;
  dialogVisible.type = "update";
  dialogVisible.title = "编辑知识";
  formData.value = {
    id: d.id,
    key: d.key,
    summary: d.summary,
    body: d.body,
    knowledge_type: d.knowledge_type,
    representative_sql: d.representative_sql,
    usage_mode: d.usage_mode,
    confidence: d.confidence,
    caliber_break_date: d.caliber_break_date ?? null,
    caliber_break_note: d.caliber_break_note ?? null,
    refs: (d.refs || []).map((r) => ({ ...r })),
  };
  editorTab.value = "edit";
  dialogVisible.visible = true;
}
async function handleDetail(id: number) {
  const res = await KnowledgeAPI.detailKnowledge(id);
  detailData.value = res.data.data;
  dialogVisible.type = "detail";
  dialogVisible.title = "知识详情";
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
      await KnowledgeAPI.updateKnowledge(formData.value.id, formData.value);
    } else {
      await KnowledgeAPI.createKnowledge(formData.value);
    }
    ElMessage.success("保存成功");
    dialogVisible.visible = false;
    await refreshData();
  } catch {
    /* 全局处理 */
  } finally {
    submitLoading.value = false;
  }
}

async function deleteRow(id: number) {
  try {
    await confirmDelete();
    await KnowledgeAPI.deleteKnowledge([id]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    /* 取消 */
  }
}
async function changeStatus(row: KnowledgeTable, target: number) {
  // 源守卫必然拦下这一步，先说清该做什么，别让用户去撞一个只有报错的按钮
  if (target === 2 && blockedByReviewGuard(row)) {
    ElMessage.warning("LLM 生成的知识需先「标记已复核」才能发布");
    return;
  }
  try {
    await ElMessageBox.confirm(`确认将「${row.key}」置为「${(STATUS_MAP[target] ?? STATUS_MAP[0]!).text}」?`, "状态流转", { type: "warning" });
    await KnowledgeAPI.changeStatus(row.id!, target);
    ElMessage.success("状态已更新");
    await refreshData();
  } catch (e) {
    if (e !== "cancel") ElMessage.error("状态流转失败");
  }
}

/**
 * 标记已复核。
 *
 * 这是 LLM 源守卫的唯一解法：后端 change_status 要求 `source=llm` 的知识必须有
 * reviewed_at 才能升「已发布」，而 reviewed_at 只能由本动作产生（create/update
 * 都刻意剥掉了该入参，否则 LLM 链路能自己盖章）。此前前端没有这个入口，
 * 那道守卫在 UI 上是死门 —— 点发布报「需先经人工复核」，却没有任何地方能复核。
 */
async function markReviewed(row: KnowledgeTable) {
  try {
    await ElMessageBox.confirm(
      `确认已人工复核「${row.key}」的正文与口径？\n`
      + `复核后会记下复核时间${blockedByReviewGuard(row) ? "，该条即可发布" : "，并清掉待复核标记"}。`,
      "标记已复核",
      { type: "warning", confirmButtonText: "已复核", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await KnowledgeAPI.markReviewed(row.id!);
    ElMessage.success("已标记复核");
    await Promise.all([refreshData(), staleVisible.value ? loadStale() : Promise.resolve()]);
  } catch (e: any) {
    ElMessage.error(e?.message || "标记复核失败");
  }
}

function buildRowActions(row: KnowledgeTable): TableOperationAction[] {
  const s = row.status;
  const all: TableOperationAction[] = [
    { key: "detail", label: "详情", artType: "view", perm: "module_semantic:knowledge:detail", run: () => handleDetail(row.id!) },
    { key: "edit", label: "编辑", artType: "edit", perm: "module_semantic:knowledge:update", run: () => handleEdit(row.id!) },
  ];
  if (s === 0) all.push({ key: "submit", label: "提审", artType: "view", perm: "module_semantic:knowledge:status", run: () => changeStatus(row, 1) });
  if (s === 1) all.push({ key: "publish", label: "发布", artType: "view", perm: "module_semantic:knowledge:status", run: () => changeStatus(row, 2) });
  if (s === 2) all.push({ key: "withdraw", label: "撤回", artType: "view", perm: "module_semantic:knowledge:status", run: () => changeStatus(row, 1) });
  // 复核入口：被守卫拦住的(LLM 未复核)、或被标记待复核的才给 —— 常态下别塞满操作列
  if (blockedByReviewGuard(row) || row.needs_review) {
    all.push({ key: "review", label: "标记已复核", artType: "view", perm: "module_semantic:knowledge:update", run: () => markReviewed(row) });
  }
  all.push({ key: "delete", label: "删除", artType: "delete", perm: "module_semantic:knowledge:delete", run: () => deleteRow(row.id!) });
  return all.filter((a) => a.perm != null && hasAuth(a.perm));
}

// ---- 治理：待复核清单 + 悬空引用扫描 ----

const staleVisible = ref(false);
const staleLoading = ref(false);
const staleList = ref<KnowledgeTable[]>([]);
const scanning = ref(false);

async function loadStale() {
  staleLoading.value = true;
  try {
    const res = await KnowledgeAPI.listStale();
    staleList.value = res.data.data ?? [];
  } catch {
    staleList.value = []; // 治理面板读失败不该让整页崩
  } finally {
    staleLoading.value = false;
  }
}

function openStale() {
  staleVisible.value = true;
  void loadStale();
}

/**
 * 扫描悬空引用。
 *
 * 写入时已校验引用存在，本扫描防的是「知识挂好之后目标被下线」—— 这类知识仍在被
 * always/auto 注入，讲的却是已经不存在的东西。扫出来即标 needs_review 进本清单。
 */
async function handleScanOrphan() {
  scanning.value = true;
  try {
    const res = await KnowledgeAPI.scanOrphanRefs();
    const ids = res.data.data ?? [];
    if (ids.length) {
      ElMessage.warning(`发现 ${ids.length} 条引用已失效，已标记待复核`);
    } else {
      ElMessage.success("未发现悬空引用");
    }
    await Promise.all([loadStale(), refreshData()]);
  } catch (e: any) {
    ElMessage.error(e?.message || "扫描失败");
  } finally {
    scanning.value = false;
  }
}

const { columns, columnChecks, data, loading, pagination, getData, replaceSearchParams, resetSearchParams, handleSizeChange, handleCurrentChange, refreshData, refreshRemove } = useTable({
  core: {
    apiFn: KnowledgeAPI.listKnowledge,
    apiParams: { page_no: 1, page_size: 10 } as KnowledgePageQuery,
    columnsFactory: () => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "key", label: "键", minWidth: 160, showOverflowTooltip: true },
      { prop: "summary", label: "摘要", minWidth: 220, showOverflowTooltip: true },
      { prop: "knowledge_type", label: "类型", width: 100, formatter: (row: KnowledgeTable) => TYPE_MAP[row.knowledge_type] || row.knowledge_type },
      { prop: "usage_mode", label: "使用模式", width: 90 },
      { prop: "refs", label: "引用数", width: 70, align: "center", formatter: (row: KnowledgeTable) => (row.refs?.length ?? 0) },
      {
        // 来源此前完全不显示，于是"这条为什么发布不了"在页面上无从判断
        prop: "source",
        label: "来源",
        width: 80,
        formatter: (row: KnowledgeTable) => {
          const m = SOURCE_MAP[row.source] ?? { type: "info", text: row.source || "-" };
          return h("span", { class: `el-tag el-tag--${m.type} el-tag--small`, style: "padding:0 8px" }, m.text);
        },
      },
      {
        prop: "status",
        label: "状态",
        width: 90,
        formatter: (row: KnowledgeTable) => {
          const m = STATUS_MAP[row.status] ?? STATUS_MAP[0]!;
          return h("span", { class: `el-tag el-tag--${m.type} el-tag--small`, style: "padding:0 8px" }, m.text);
        },
      },
      {
        // 复核态：needs_review(级联失效/扫描标记) 与"被源守卫拦住"是两回事，分开显示 ——
        // 前者是"该重看一眼", 后者是"想发布但发不了", 混成一个标记运维分不清该做什么
        prop: "needs_review",
        label: "复核",
        width: 96,
        align: "center",
        formatter: (row: KnowledgeTable) => {
          if (row.needs_review) {
            return h("span", { class: "el-tag el-tag--danger el-tag--small", style: "padding:0 8px" }, "待复核");
          }
          if (blockedByReviewGuard(row)) {
            return h("span", { class: "el-tag el-tag--warning el-tag--small", style: "padding:0 8px" }, "未复核");
          }
          return row.reviewed_at ? h("span", { class: "text-xs text-gray-400" }, row.reviewed_at.slice(0, 10)) : "-";
        },
      },
      { prop: "updated_time", label: "更新时间", width: 168, showOverflowTooltip: true },
      {
        prop: "operation",
        label: "操作",
        width: 240,
        fixed: "right",
        align: "center",
        formatter: (row: KnowledgeTable) => renderTableOperationCell(buildRowActions(row), { wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1" }),
      },
    ],
  },
});

async function handleSearchBarSearch(params: Record<string, unknown>) {
  replaceSearchParams({
    key: params.key as string | undefined,
    summary: params.summary as string | undefined,
    knowledge_type: params.knowledge_type as string | undefined,
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
    await KnowledgeAPI.deleteKnowledge(ids);
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
.kb-hint {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-placeholder);
}

.kb-editor {
  width: 100%;
  &__toolbar {
    margin-bottom: 6px;
  }
}
.kb-preview {
  min-height: 120px;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-blank);
}
.kb-sql {
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-size: 0.8rem;
  white-space: pre-wrap;
  word-break: break-all;
}
.kb-refs {
  width: 100%;
  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
}
</style>
