<template>
  <div class="fa-card-list">
    <slot name="header" />
    <div v-loading="loading">
      <ElEmpty v-if="!loading && items.length === 0" :description="emptyText" />
      <div v-else :class="gridClass">
        <div
          v-for="(item, index) in items"
          :key="item[keyField] ?? index"
          class="fa-card-item border border-g-300/60 dark:border-g-700 rounded-xl overflow-hidden bg-white dark:bg-g-800 hover:shadow-lg transition-all duration-300"
          @click="$emit('itemClick', item)"
        >
          <slot name="card" :item="item" :index="index" />
        </div>
      </div>
      <div v-if="total > pageSize" class="flex justify-center mt-6">
        <ElPagination
          :current-page="currentPage"
          :total="total"
          :page-size="pageSize"
          @current-change="$emit('pageChange', $event)"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

defineOptions({ name: "FaCardList" });

interface Props {
  items: any[];
  total?: number;
  pageSize?: number;
  currentPage?: number;
  loading?: boolean;
  columns?: number;
  keyField?: string;
  emptyText?: string;
}

const props = withDefaults(defineProps<Props>(), {
  total: 0,
  pageSize: 12,
  currentPage: 1,
  loading: false,
  columns: 4,
  keyField: "id",
  emptyText: "暂无数据",
});

interface Emits {
  pageChange: [page: number];
  itemClick: [item: any];
}

defineEmits<Emits>();

const gridClass = computed(() => {
  const cols = {
    2: "grid grid-cols-2 gap-4 max-lg:grid-cols-1",
    3: "grid grid-cols-3 gap-4 max-xl:grid-cols-2 max-lg:grid-cols-1",
    4: "grid grid-cols-4 gap-4 max-2xl:grid-cols-3 max-xl:grid-cols-2 max-lg:grid-cols-1",
    5: "grid grid-cols-5 gap-5 max-2xl:grid-cols-4 max-xl:grid-cols-3 max-lg:grid-cols-2 max-sm:grid-cols-1",
  };
  return cols[props.columns as keyof typeof cols] || cols[4];
});
</script>

<style scoped>
.fa-card-item {
  cursor: pointer;
}
</style>
