<!-- 物理目录：语义中心 - L1 物理表/列浏览(只读, 从 DataSage 同步) -->
<template>
  <div class="catalog-page flex gap-3 fa-full-height">
    <!-- 左：数据源 + 库 -->
    <ElCard class="catalog-side" body-class="catalog-side__body">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-medium">物理目录</span>
          <div class="flex gap-2">
            <ElButton v-if="hasAuth('module_semantic:catalog:manage')" size="small" plain @click="openManage">
              数据源管理
            </ElButton>
            <ElButton v-if="hasAuth('module_semantic:catalog:sync')" size="small" type="primary" plain @click="openSync">
              同步
            </ElButton>
          </div>
        </div>
      </template>
      <ElSelect v-model="currentDsId" placeholder="选择数据源" class="w-full mb-2" @change="onDsChange">
        <ElOption v-for="ds in datasources" :key="ds.id" :label="ds.name" :value="ds.id" />
      </ElSelect>
      <ElScrollbar class="catalog-db-list">
        <div
          v-for="db in databases"
          :key="db.id"
          class="catalog-db-item"
          :class="{ active: currentDbId === db.id }"
          @click="onDbChange(db)"
        >
          <FaSvgIcon icon="ri:database-2-line" class="mr-1" />
          <span class="flex-1 truncate">{{ db.name }}</span>
          <span class="catalog-db-count">{{ db.table_count }}</span>
        </div>
        <ElEmpty v-if="!databases.length" description="无库(先同步)" :image-size="50" />
      </ElScrollbar>
    </ElCard>

    <!-- 右：表列表 -->
    <ElCard class="catalog-main flex-1">
      <FaTableHeader v-model:columns="columnChecks" :loading="loading" :show-search-bar="false" @refresh="refreshData">
        <template #left>
          <ElInput v-model="tableKeyword" placeholder="搜索表名" clearable class="w-60" @keyup.enter="doSearch">
            <template #append><ElButton @click="doSearch">搜索</ElButton></template>
          </ElInput>
        </template>
      </FaTableHeader>
      <FaTable
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <!-- 表详情(列) 抽屉 -->
    <FaDrawer v-model="detailVisible" title="表详情" size="720px" form-mode="detail" @cancel="detailVisible = false" @confirm="detailVisible = false">
      <ElDescriptions :column="2" border size="small" class="mb-3">
        <ElDescriptionsItem label="表名">{{ tableDetail?.name }}</ElDescriptionsItem>
        <ElDescriptionsItem label="库">{{ tableDetail?.db_name }}</ElDescriptionsItem>
        <ElDescriptionsItem label="类型">{{ tableDetail?.table_type }}</ElDescriptionsItem>
        <ElDescriptionsItem label="行数">{{ tableDetail?.row_count ?? "-" }}</ElDescriptionsItem>
        <ElDescriptionsItem label="注释" :span="2">{{ tableDetail?.comment || "-" }}</ElDescriptionsItem>
      </ElDescriptions>
      <div class="font-medium mb-2">列（{{ tableDetail?.columns?.length ?? 0 }}）</div>
      <ElTable :data="tableDetail?.columns || []" size="small" border max-height="60vh">
        <ElTableColumn prop="ordinal" label="#" width="50" />
        <ElTableColumn prop="name" label="列名" width="160" show-overflow-tooltip />
        <ElTableColumn prop="column_type_full" label="类型" width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.column_type_full || row.col_type }}</template>
        </ElTableColumn>
        <ElTableColumn prop="key_type" label="键" width="80" />
        <ElTableColumn label="可空" width="60">
          <template #default="{ row }">{{ row.is_nullable ? "是" : "否" }}</template>
        </ElTableColumn>
        <ElTableColumn prop="comment" label="注释" show-overflow-tooltip />
      </ElTable>
    </FaDrawer>

    <!-- 同步对话框 -->
    <FaDialog v-model="syncVisible" title="从 DataSage 同步物理元数据" width="560px" form-mode="create" confirm-text="开始同步" :confirm-loading="syncing" @cancel="syncVisible = false" @confirm="doSync">
      <ElForm label-width="130px">
        <ElFormItem label="数据源 UUID" required>
          <ElInput v-model="syncExternalId" placeholder="DataSage 数据源 UUID(从 DataSage 界面复制)" />
        </ElFormItem>
        <ElFormItem label="名称(可选)">
          <ElInput v-model="syncName" placeholder="本地登记的数据源名称,便于识别" />
        </ElFormItem>
        <ElFormItem label="指定库(可选)">
          <ElInput v-model="syncDbNames" placeholder="逗号分隔,如 dws,ads;留空则同步该数据源全部库" />
        </ElFormItem>
        <ElAlert type="info" :closable="false" show-icon>
          DataSage 走飞书登录,故这里手动填数据源 UUID(catalog 接口免鉴权)。首次同步某库会回源数仓,可能较慢;同步幂等,可重复执行。
        </ElAlert>
      </ElForm>
    </FaDialog>

    <!-- 数据源管理对话框 -->
    <FaDialog v-model="manageVisible" title="数据源管理" width="920px" form-mode="detail" confirm-text="关闭" @confirm="manageVisible = false">
      <div class="flex justify-end mb-2">
        <ElButton size="small" type="primary" @click="openEditDs()">登记数据源</ElButton>
      </div>
      <ElTable v-loading="manageLoading" :data="datasources" size="small" border max-height="50vh">
        <ElTableColumn prop="name" label="名称" min-width="140" show-overflow-tooltip />
        <ElTableColumn label="来源" width="90" align="center">
          <template #default="{ row }">
            <ElTag :type="row.source === 'manual' ? 'success' : 'info'" size="small">
              {{ row.source === "manual" ? "自采集" : "DataSage" }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="ds_type" label="类型" width="90">
          <template #default="{ row }">{{ formatDatasourceType(row.ds_type) }}</template>
        </ElTableColumn>
        <ElTableColumn label="地址" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.host ? `${row.host}:${row.port}` : "-" }}</template>
        </ElTableColumn>
        <ElTableColumn prop="username" label="账号" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.username || "-" }}</template>
        </ElTableColumn>
        <ElTableColumn prop="synced_at" label="最近同步" width="160" show-overflow-tooltip />
        <ElTableColumn label="操作" width="250" fixed="right" align="center">
          <template #default="{ row }">
            <template v-if="row.source === 'manual'">
              <ElButton size="small" link type="primary" :loading="testingId === row.id" @click="doTest(row)">测试连接</ElButton>
              <ElButton size="small" link type="primary" @click="openCollect(row)">采集</ElButton>
              <ElButton size="small" link type="primary" @click="openEditDs(row)">编辑</ElButton>
              <ElButton size="small" link type="danger" @click="doDeleteDs(row)">删除</ElButton>
            </template>
            <span v-else class="text-secondary text-xs">由 DataSage 同步</span>
          </template>
        </ElTableColumn>
      </ElTable>
    </FaDialog>

    <!-- 登记/编辑数据源对话框 -->
    <FaDialog
      v-model="editDsVisible"
      :title="editDsForm.id ? '编辑数据源' : '登记数据源'"
      width="560px"
      :form-mode="editDsForm.id ? 'update' : 'create'"
      :confirm-loading="editDsSaving"
      @cancel="editDsVisible = false"
      @confirm="doSaveDs"
    >
      <ElForm label-width="110px">
        <ElFormItem label="名称" required>
          <ElInput v-model="editDsForm.name" placeholder="便于识别的数据源名称" maxlength="100" />
        </ElFormItem>
        <ElFormItem label="类型">
          <ElSelect v-model="editDsForm.ds_type" class="w-full" @change="onEditDsTypeChange">
            <ElOption v-for="option in DATASOURCE_TYPE_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="主机" required>
          <ElInput v-model="editDsForm.host" placeholder="如 10.0.0.1 或 starrocks.internal" maxlength="200" />
        </ElFormItem>
        <ElFormItem label="端口" required>
          <ElInputNumber v-model="editDsForm.port" :min="1" :max="65535" class="w-full" />
        </ElFormItem>
        <ElFormItem label="账号" required>
          <ElInput v-model="editDsForm.username" placeholder="连接账号(需 information_schema 读权限)" maxlength="100" />
        </ElFormItem>
        <ElFormItem label="密码">
          <ElInput
            v-model="editDsForm.password"
            type="password"
            show-password
            :placeholder="editDsForm.id ? '留空则不修改' : '连接密码(加密存储)'"
          />
        </ElFormItem>
        <ElFormItem label="默认库">
          <ElInput v-model="editDsForm.default_database" placeholder="可选,仅用于建连" maxlength="100" />
        </ElFormItem>
        <ElFormItem label="描述">
          <ElInput v-model="editDsForm.description" type="textarea" :rows="2" maxlength="500" />
        </ElFormItem>
        <ElAlert type="info" :closable="false" show-icon>密码使用平台密钥加密存储;采集只读 information_schema,不会触碰业务数据。</ElAlert>
      </ElForm>
    </FaDialog>

    <!-- 触发采集对话框 -->
    <FaDialog
      v-model="collectVisible"
      :title="`采集元数据 - ${collectDs?.name ?? ''}`"
      width="520px"
      form-mode="create"
      confirm-text="开始采集"
      :confirm-loading="collecting"
      @cancel="collectVisible = false"
      @confirm="doCollect"
    >
      <ElForm label-width="110px">
        <ElFormItem label="指定库">
          <ElInput v-model="collectDbNames" placeholder="逗号分隔,如 dws,ads;留空则采集全部非系统库" />
        </ElFormItem>
        <ElAlert type="warning" :closable="false" show-icon>
          将连接 {{ collectDs?.host }}:{{ collectDs?.port }} 扫描 information_schema,表多的库首次采集可能较慢;采集幂等,可重复执行。
        </ElAlert>
      </ElForm>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { h } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useTable } from "@/hooks/core/useTable";
import { useAuth } from "@/hooks/core/useAuth";
import CatalogAPI, {
  type CatDatabase,
  type CatDatasource,
  type CatTable,
  type CatTableDetail,
  type CatTablePageQuery,
} from "@/api/module_semantic/catalog";
import {
  DATASOURCE_TYPE_OPTIONS,
  buildDatasourceBody,
  createDatasourceForm,
  createDatasourceFormFromRow,
  defaultDatasourcePort,
  formatDatasourceType,
  type DatasourceForm,
} from "./form";

defineOptions({ name: "SemanticCatalog", inheritAttrs: false });

const { hasAuth } = useAuth();

const datasources = ref<CatDatasource[]>([]);
const databases = ref<CatDatabase[]>([]);
const currentDsId = ref<number>();
const currentDbId = ref<number>();
const tableKeyword = ref("");

async function loadDatasources() {
  const res = await CatalogAPI.listDatasource();
  datasources.value = res.data.data || [];
  if (datasources.value.length && !currentDsId.value) {
    currentDsId.value = datasources.value[0]!.id;
    await loadDatabases();
  }
}
async function loadDatabases() {
  if (!currentDsId.value) return;
  const res = await CatalogAPI.listDatabase(currentDsId.value);
  databases.value = res.data.data || [];
}
async function onDsChange() {
  currentDbId.value = undefined;
  await loadDatabases();
}
async function onDbChange(db: CatDatabase) {
  currentDbId.value = db.id;
  replaceSearchParams({ database_id: db.id, name: undefined });
  getData();
}
function doSearch() {
  replaceSearchParams({ database_id: currentDbId.value, name: tableKeyword.value || undefined });
  getData();
}

// 表详情
const detailVisible = ref(false);
const tableDetail = ref<CatTableDetail>();
async function openDetail(id: number) {
  const res = await CatalogAPI.tableDetail(id);
  tableDetail.value = res.data.data;
  detailVisible.value = true;
}

// 同步(DataSage 走飞书登录,手动填数据源 UUID;catalog GET 免鉴权)
const syncVisible = ref(false);
const syncing = ref(false);
const syncExternalId = ref<string>();
const syncName = ref("");
const syncDbNames = ref("");
function openSync() {
  syncVisible.value = true;
}
async function doSync() {
  if (!syncExternalId.value?.trim()) {
    ElMessage.warning("请填写 DataSage 数据源 UUID");
    return;
  }
  syncing.value = true;
  try {
    const dbNames = syncDbNames.value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const res = await CatalogAPI.sync({
      external_datasource_id: syncExternalId.value.trim(),
      name: syncName.value.trim() || undefined,
      db_names: dbNames.length ? dbNames : undefined,
    });
    ElMessage.success(res.data.data?.message || "同步完成");
    syncVisible.value = false;
    await loadDatasources();
    await loadDatabases();
  } catch {
    ElMessage.error("同步失败,请检查 DataSage 连接与数据源 UUID");
  } finally {
    syncing.value = false;
  }
}

// ---- 数据源管理(自采集, source=manual) ----
const manageVisible = ref(false);
const manageLoading = ref(false);
async function openManage() {
  manageVisible.value = true;
  manageLoading.value = true;
  try {
    await loadDatasources();
  } finally {
    manageLoading.value = false;
  }
}

const editDsVisible = ref(false);
const editDsSaving = ref(false);
const editDsForm = ref<DatasourceForm>(createDatasourceForm());
function openEditDs(row?: CatDatasource) {
  editDsForm.value = createDatasourceFormFromRow(row);
  editDsVisible.value = true;
}
function onEditDsTypeChange(value: string) {
  editDsForm.value.port = defaultDatasourcePort(value);
}
async function doSaveDs() {
  const f = editDsForm.value;
  if (!f.name?.trim() || !f.host?.trim() || !f.port || !f.username?.trim()) {
    ElMessage.warning("名称/主机/端口/账号必填");
    return;
  }
  editDsSaving.value = true;
  try {
    const body = buildDatasourceBody(f);
    if (f.id) {
      await CatalogAPI.updateDatasource(f.id, body);
    } else {
      await CatalogAPI.createDatasource(body);
    }
    ElMessage.success(f.id ? "更新成功" : "登记成功");
    editDsVisible.value = false;
    await loadDatasources();
  } catch {
    ElMessage.error("保存失败,请检查填写内容");
  } finally {
    editDsSaving.value = false;
  }
}

// 测试连接
const testingId = ref<number>();
async function doTest(row: CatDatasource) {
  testingId.value = row.id;
  try {
    await CatalogAPI.testDatasource(row.id);
    ElMessage.success(`「${row.name}」连接成功`);
  } catch (e: any) {
    ElMessage.error(e?.message || `「${row.name}」连接失败`);
  } finally {
    testingId.value = undefined;
  }
}

// 删除
async function doDeleteDs(row: CatDatasource) {
  await ElMessageBox.confirm(`删除数据源「${row.name}」?其下已采集的库表缓存会一并删除。`, "确认删除", { type: "warning" });
  await CatalogAPI.deleteDatasource([row.id]);
  ElMessage.success("删除成功");
  if (currentDsId.value === row.id) {
    currentDsId.value = undefined;
    databases.value = [];
  }
  await loadDatasources();
}

// 采集
const collectVisible = ref(false);
const collecting = ref(false);
const collectDs = ref<CatDatasource>();
const collectDbNames = ref("");
function openCollect(row: CatDatasource) {
  collectDs.value = row;
  collectDbNames.value = "";
  collectVisible.value = true;
}
async function doCollect() {
  if (!collectDs.value) return;
  collecting.value = true;
  try {
    const dbNames = collectDbNames.value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const res = await CatalogAPI.collect({
      datasource_id: collectDs.value.id,
      db_names: dbNames.length ? dbNames : undefined,
    });
    ElMessage.success(res.data.data?.message || "采集完成");
    collectVisible.value = false;
    manageVisible.value = false;
    await loadDatasources();
    if (currentDsId.value === collectDs.value.id) await loadDatabases();
  } catch (e: any) {
    ElMessage.error(e?.message || "采集失败,请检查连接信息与账号权限");
  } finally {
    collecting.value = false;
  }
}

const { columns, columnChecks, data, loading, pagination, getData, replaceSearchParams, handleSizeChange, handleCurrentChange, refreshData } = useTable({
  core: {
    apiFn: CatalogAPI.listTable,
    apiParams: { page_no: 1, page_size: 20 } as CatTablePageQuery,
    immediate: false,
    columnsFactory: () => [
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "name", label: "表名", minWidth: 180, showOverflowTooltip: true },
      { prop: "table_type", label: "类型", width: 100 },
      { prop: "row_count", label: "行数", width: 100, align: "right" },
      { prop: "comment", label: "注释", minWidth: 200, showOverflowTooltip: true },
      { prop: "synced_at", label: "同步时间", width: 168, showOverflowTooltip: true },
      {
        prop: "operation",
        label: "操作",
        width: 100,
        fixed: "right",
        align: "center",
        formatter: (row: CatTable) => {
          return h(
            "a",
            { class: "el-button el-button--primary is-link is-text", style: "cursor:pointer", onClick: () => openDetail(row.id) },
            "查看列"
          );
        },
      },
    ],
  },
});

onMounted(loadDatasources);
</script>

<style scoped lang="scss">
.catalog-page {
  align-items: stretch;
}
.catalog-side {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  :deep(.catalog-side__body) {
    display: flex;
    flex-direction: column;
    min-height: 0;
    flex: 1;
  }
}
.catalog-db-list {
  flex: 1;
  min-height: 0;
}
.catalog-db-item {
  display: flex;
  align-items: center;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
  &:hover {
    background: var(--el-fill-color-light);
  }
  &.active {
    background: var(--el-color-primary-light-9);
    color: var(--el-color-primary);
  }
}
.catalog-db-count {
  font-size: 0.72rem;
  color: var(--el-text-color-secondary);
}
.catalog-main {
  min-width: 0;
}
</style>
