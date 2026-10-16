<script setup lang="ts">
import { onMounted, ref } from "vue";
import CatalogAPI, {
  type CatColumn,
  type CatDatasource,
  type CatDatabase,
  type CatTable,
} from "@/api/module_semantic/catalog";

const props = defineProps<{ modelValue?: number }>();
const emit = defineEmits<{
  (e: "update:modelValue", v: number | undefined): void;
  (e: "update:meta", v: { datasource?: string; database?: string; table?: string }): void;
  (e: "update:columns", v: CatColumn[]): void;
}>();

const datasources = ref<CatDatasource[]>([]);
const databases = ref<CatDatabase[]>([]);
const tables = ref<CatTable[]>([]);
const dsId = ref<number>();
const dbId = ref<number>();
const loading = ref(false);
let databaseRequestId = 0;
let tableListRequestId = 0;
let tableDetailRequestId = 0;

async function loadDatasources() {
  try {
    const res = await CatalogAPI.listDatasource();
    datasources.value = res.data.data ?? [];
  } catch (e) {
    console.warn("[PhysicalTableSelect] 加载数据源失败,降级为空", e);
    datasources.value = [];
  }
}
async function onDatasourceChange(id?: number | null) {
  const nextId = id ?? undefined;
  const requestId = ++databaseRequestId;
  tableListRequestId += 1;
  tableDetailRequestId += 1;
  dsId.value = nextId;
  dbId.value = undefined;
  databases.value = [];
  tables.value = [];
  emit("update:modelValue", undefined);
  emit("update:columns", []);
  if (!nextId) return;
  try {
    const res = await CatalogAPI.listDatabase(nextId);
    if (requestId !== databaseRequestId || dsId.value !== nextId) return;
    databases.value = res.data.data ?? [];
  } catch (e) {
    if (requestId === databaseRequestId && dsId.value === nextId) {
      console.warn("[PhysicalTableSelect] 加载数据库失败,降级为空", e);
      databases.value = [];
    }
  }
}
async function onDatabaseChange(id?: number | null) {
  const nextId = id ?? undefined;
  tableListRequestId += 1;
  tableDetailRequestId += 1;
  dbId.value = nextId;
  tables.value = [];
  emit("update:modelValue", undefined);
  emit("update:columns", []);
  if (!nextId) return;
  await searchTables("");
}
async function searchTables(kw: string) {
  const databaseId = dbId.value;
  if (!databaseId) {
    tableListRequestId += 1;
    tables.value = [];
    return;
  }
  const requestId = ++tableListRequestId;
  loading.value = true;
  try {
    const res = await CatalogAPI.listTable({
      database_id: databaseId,
      name: kw || undefined,
      page_no: 1,
      page_size: 50,
    });
    if (requestId !== tableListRequestId || dbId.value !== databaseId) return;
    tables.value = res.data.data?.items ?? [];
  } catch (e) {
    if (requestId === tableListRequestId && dbId.value === databaseId) {
      console.warn("[PhysicalTableSelect] 加载物理表失败,降级为空", e);
      tables.value = [];
    }
  } finally {
    if (requestId === tableListRequestId) loading.value = false;
  }
}
async function emitColumnsForTable(id: number) {
  const requestId = ++tableDetailRequestId;
  emit("update:columns", []);
  try {
    const res = await CatalogAPI.tableDetail(id);
    if (requestId !== tableDetailRequestId) return;
    emit("update:columns", res.data.data?.columns ?? []);
  } catch (e) {
    if (requestId === tableDetailRequestId) emit("update:columns", []);
    console.warn("[PhysicalTableSelect] 加载物理列失败,降级为空", e);
  }
}
async function onTableChange(id?: number | null) {
  const nextId = id ?? undefined;
  const t = tables.value.find((x) => x.id === nextId);
  const ds = datasources.value.find((x) => x.id === dsId.value);
  const db = databases.value.find((x) => x.id === dbId.value);
  emit("update:modelValue", nextId);
  if (!nextId) {
    tableDetailRequestId += 1;
    emit("update:meta", {});
    emit("update:columns", []);
    return;
  }
  emit("update:meta", { datasource: ds?.name, database: t?.db_name ?? db?.name, table: t?.name });
  await emitColumnsForTable(nextId);
}

// 编辑态回显预载: 挂载时若父组件传入了初始 modelValue(cat_table.id),
// 拉该表详情把表名塞进 tables 列表并回填 dbId,使下拉能显示已绑的表名。
// tableDetail 的返回体 CatTableDetail extends CatTable,含 id/name/db_name/database_id。
// datasource 无法从表详情直接得到(链路太长),故只回填到"库"这一级;
// 数据源下拉留空但不报错。
async function preloadSelected(id: number) {
  const requestId = ++tableDetailRequestId;
  try {
    const res = await CatalogAPI.tableDetail(id);
    if (requestId !== tableDetailRequestId) return;
    const t = res.data.data;
    if (!t) return;
    // 至少保证表名可回显: 把该表放进 tables(去重)
    if (!tables.value.some((x) => x.id === t.id)) {
      tables.value = [t as CatTable, ...tables.value];
    }
    // 尽量回填 dbId(=database_id),使"库"下拉的 disabled/回显链条不断
    if (t.database_id != null && dbId.value == null) {
      dbId.value = t.database_id;
    }
    emit("update:columns", t.columns ?? []);
  } catch (e) {
    console.warn("[PhysicalTableSelect] 回显预载已绑物理表失败,降级为空", e);
  }
}

loadDatasources();

// mounted 哨兵: 回显预载只在挂载时按初值做一次,不与用户后续级联操作打架。
let mounted = false;
onMounted(() => {
  if (!mounted && props.modelValue != null) preloadSelected(props.modelValue);
  mounted = true;
});

defineExpose({
  onDatasourceChange,
  onDatabaseChange,
  onTableChange,
  searchTables,
  preloadSelected,
  tables,
  databases,
  dsId,
  dbId,
});
</script>

<template>
  <div class="physical-table-select" data-role="physical-table-select">
    <ElSelect
      :model-value="dsId"
      placeholder="数据源"
      clearable
      filterable
      class="physical-table-select__control physical-table-select__datasource"
      @update:model-value="onDatasourceChange"
    >
      <ElOption v-for="d in datasources" :key="d.id" :label="d.name" :value="d.id" />
    </ElSelect>
    <ElSelect
      :model-value="dbId"
      placeholder="库"
      clearable
      filterable
      class="physical-table-select__control physical-table-select__database"
      :disabled="!dsId"
      @update:model-value="onDatabaseChange"
    >
      <ElOption v-for="d in databases" :key="d.id" :label="d.name" :value="d.id" />
    </ElSelect>
    <ElSelect
      :model-value="props.modelValue"
      placeholder="表"
      clearable
      filterable
      remote
      :remote-method="searchTables"
      :loading="loading"
      :disabled="!dbId"
      class="physical-table-select__control physical-table-select__table"
      data-role="physical-table-select-table"
      @update:model-value="onTableChange"
    >
      <ElOption v-for="t in tables" :key="t.id" :label="t.name" :value="t.id" />
    </ElSelect>
  </div>
</template>

<style scoped lang="scss">
.physical-table-select {
  display: grid;
  grid-template-columns: minmax(150px, 0.8fr) minmax(180px, 0.9fr) minmax(280px, 1.6fr);
  gap: 8px;
  width: 100%;
  min-width: 0;

  &__control {
    width: 100%;
    min-width: 0;
  }
}

@media (width <= 760px) {
  .physical-table-select {
    grid-template-columns: 1fr;
  }
}
</style>
