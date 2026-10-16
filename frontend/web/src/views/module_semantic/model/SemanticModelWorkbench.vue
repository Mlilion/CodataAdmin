<template>
  <section class="semantic-workbench">
    <header v-if="tree" class="workbench-header">
      <div class="workbench-header__identity">
        <div class="workbench-header__eyebrow">语义模型 / {{ tree.domain_name || "未分域" }}</div>
        <div class="workbench-header__title-row">
          <h1>{{ tree.model.name }}</h1>
          <ElButton
            v-if="canUpdate"
            link
            class="workbench-header__edit"
            @click="emit('edit-model')"
          >
            <FaSvgIcon icon="ri:edit-line" />
          </ElButton>
          <ElTag size="small" effect="plain">{{ tree.domain_name || "未分域" }}</ElTag>
          <ElTag size="small" effect="plain">v{{ tree.model.version }}</ElTag>
          <ElTag size="small" :type="statusMeta.type" effect="light">{{ statusMeta.text }}</ElTag>
          <!-- 受限可见必须在标题行标出来：这是"谁能通过 MCP 问到它"的开关，
               藏在编辑对话框里的话，看模型的人不知道它是受限的 -->
          <ElTooltip
            v-if="tree.model.visibility === 'restricted'"
            content="限制可见：仅模型负责人、被授权用户与超管可经 MCP 访问"
            placement="bottom"
          >
            <ElTag size="small" type="warning" effect="light">
              <FaSvgIcon icon="ri:lock-line" class="mr-1" />限制可见
            </ElTag>
          </ElTooltip>
        </div>
        <p>
          {{ tree.model.description || "为这个模型补充分析场景说明，帮助使用者快速理解适用范围。" }}
        </p>
      </div>

      <div class="workbench-header__actions">
        <div class="completeness-ring">
          <ElProgress
            type="circle"
            :percentage="completeness"
            :width="54"
            :stroke-width="5"
            :color="completenessColor"
          />
          <div>
            <span>模型完整度</span>
            <strong>{{ completeness }}%</strong>
          </div>
        </div>
        <ElButton @click="emit('refresh')">
          <FaSvgIcon icon="ri:shield-check-line" />
          校验模型
        </ElButton>
        <ElButton @click="emit('releases')">版本记录</ElButton>
        <!-- 授权入口常显(不只在 restricted 时)：改成受限之前先看/先配授权名单，
             才不会出现"切成受限那一刻所有人都被挡在外面" -->
        <ElButton v-if="canGrant" @click="emit('grants')">
          <FaSvgIcon icon="ri:user-settings-line" />
          授权管理
        </ElButton>
        <ElButton
          v-if="canDelete"
          circle
          plain
          type="danger"
          title="删除模型"
          @click="emit('delete-model')"
        >
          <FaSvgIcon icon="ri:delete-bin-6-line" />
        </ElButton>
        <ElTooltip :disabled="tree.publishable" placement="bottom">
          <template #content>
            <div v-for="blocker in tree.publish_blockers.slice(0, 8)" :key="blocker">
              {{ blocker }}
            </div>
          </template>
          <span>
            <ElButton
              v-if="canUpdate"
              type="primary"
              :disabled="!tree.publishable"
              :loading="publishing"
              @click="emit('publish')"
            >
              <FaSvgIcon icon="ri:rocket-2-line" />
              发布版本
            </ElButton>
          </span>
        </ElTooltip>
      </div>
    </header>

    <div class="workbench-main">
      <aside class="model-rail">
        <ElInput v-model="modelSearch" placeholder="搜索模型" clearable>
          <template #prefix><FaSvgIcon icon="ri:search-line" /></template>
        </ElInput>

        <ElSelect
          :model-value="filterDomainId"
          placeholder="全部业务域"
          clearable
          class="model-rail__domain"
          @change="emit('domain-change', $event as number | undefined)"
        >
          <ElOption
            v-for="domain in domains"
            :key="domain.id"
            :label="domain.name"
            :value="domain.id!"
          />
        </ElSelect>

        <div class="rail-section-heading">
          <span>我的模型</span>
          <ElButton v-if="canCreate" link @click="emit('create-model')"
            ><FaSvgIcon icon="ri:add-line"
          /></ElButton>
        </div>

        <ElScrollbar class="model-rail__list">
          <button
            v-for="model in filteredModels"
            :key="model.id"
            type="button"
            class="model-list-item"
            :class="{ 'model-list-item--active': currentModelId === model.id }"
            @click="emit('select-model', model.id)"
          >
            <FaSvgIcon icon="ri:organization-chart" />
            <span class="model-list-item__text">
              <strong>{{ model.name }}</strong>
              <small>{{ model.code }}</small>
            </span>
            <span class="model-list-item__version">v{{ model.version }}</span>
            <span class="asset-light" :class="statusLight(model.status)" />
          </button>
          <ElEmpty v-if="!filteredModels.length" description="没有匹配的模型" :image-size="48" />
        </ElScrollbar>

        <div class="rail-section-heading rail-section-heading--assets">
          <span>资产库</span>
          <ElButton v-if="canUpdate && tree" link @click="emit('add-member')"
            ><FaSvgIcon icon="ri:add-line"
          /></ElButton>
        </div>
        <div class="asset-library">
          <button
            v-for="asset in assetLibrary"
            :key="asset.label"
            type="button"
            @click="asset.action?.()"
          >
            <FaSvgIcon :icon="asset.icon" />
            <span>{{ asset.label }}</span>
            <strong>{{ asset.count }}</strong>
            <FaSvgIcon icon="ri:arrow-right-s-line" />
          </button>
        </div>
        <button
          v-if="canUpdate && tree"
          type="button"
          class="asset-library__hint"
          @click="emit('add-member')"
        >
          <FaSvgIcon icon="ri:drag-drop-line" />
          <span>添加实体到模型，快速建立或关联语义资产</span>
        </button>
      </aside>

      <main class="model-canvas">
        <template v-if="tree">
          <div class="canvas-toolbar">
            <ElRadioGroup v-model="viewMode" size="small">
              <ElRadioButton value="graph">关系图</ElRadioButton>
              <ElRadioButton value="tree">结构树</ElRadioButton>
            </ElRadioGroup>
            <div v-if="viewMode === 'graph'" class="canvas-toolbar__actions">
              <ElButton data-testid="auto-layout" size="small" @click="resetGraphLayout">
                <FaSvgIcon icon="ri:layout-grid-line" />
                自动布局
              </ElButton>
              <ElButton data-testid="fit-canvas" size="small" circle title="适应画布" @click="fitGraph"
                ><FaSvgIcon icon="ri:aspect-ratio-line"
              /></ElButton>
              <ElButton
                data-testid="toggle-fullscreen"
                size="small"
                circle
                :title="isFullscreen ? '退出全屏 (Esc)' : '全屏查看'"
                @click="toggleFullscreen"
                ><FaSvgIcon :icon="isFullscreen ? 'ri:fullscreen-exit-line' : 'ri:fullscreen-line'"
              /></ElButton>
            </div>
            <div v-else class="canvas-toolbar__tree-meta">完整语义资产结构</div>
          </div>

          <div
            v-if="viewMode === 'graph'"
            class="relationship-canvas"
            :class="{ 'relationship-canvas--fullscreen': isFullscreen }"
          >
            <ElButton
              v-if="isFullscreen"
              class="relationship-canvas__exit"
              size="small"
              @click="toggleFullscreen"
            >
              <FaSvgIcon icon="ri:fullscreen-exit-line" />
              退出全屏 (Esc)
            </ElButton>
            <VueFlow
              :key="layoutVersion"
              ref="flowRef"
              :data-layout-version="layoutVersion"
              :nodes="graph.nodes"
              :edges="graph.edges"
              :min-zoom="0.35"
              :max-zoom="1"
              :default-viewport="{ zoom: 0.82 }"
              fit-view-on-init
              @node-click="onGraphNodeClick"
              @edge-click="onGraphEdgeClick"
            >
              <Background :gap="20" :size="1" pattern-color="#dbe5f1" />
              <Controls position="bottom-left" />
              <MiniMap pannable zoomable position="bottom-right" :node-color="miniMapColor" />
              <template #node-entity="nodeProps">
                <EntityGraphNode :data="nodeProps.data" />
              </template>
            </VueFlow>
            <ElEmpty
              v-if="!graph.nodes.length"
              description="先从左侧添加实体，开始组装语义模型"
              :image-size="72"
              class="canvas-empty"
            />
          </div>

          <div v-else class="structure-tree">
            <ElTree
              :data="treeData"
              node-key="key"
              :props="{ label: 'label', children: 'children' }"
              :default-expanded-keys="expandedKeys"
              highlight-current
              :expand-on-click-node="false"
              @node-click="onTreeNodeClick"
            >
              <template #default="{ data }">
                <span class="tree-row">
                  <span
                    v-if="data.light"
                    class="asset-light"
                    :class="`asset-light--${data.light}`"
                  />
                  <FaSvgIcon :icon="data.icon" :style="{ color: data.color }" />
                  <strong>{{ data.label }}</strong>
                  <small v-if="data.sub">{{ data.sub }}</small>
                  <ElTag v-if="data.status !== undefined" size="small" effect="plain">
                    {{ statusText(data.status) }}
                  </ElTag>
                </span>
              </template>
            </ElTree>
          </div>
        </template>
        <ElEmpty
          v-else-if="!treeLoading"
          description="选择左侧模型查看工作台，或新建一个语义模型"
          :image-size="92"
        />
      </main>

      <aside class="model-inspector" :class="{ 'model-inspector--open': inspectorOpen }">
        <button
          type="button"
          class="inspector-close"
          data-testid="close-inspector"
          aria-label="关闭属性面板"
          @click="inspectorOpen = false"
        >
          <FaSvgIcon icon="ri:close-line" />
        </button>
        <template v-if="selected">
          <div class="inspector-heading">
            <div>
              <small>{{ inspectorKind }}</small>
              <h2>{{ selected.title }}</h2>
            </div>
            <span
              class="inspector-status"
              :class="`inspector-status--${selected.light ?? 'neutral'}`"
            >
              {{ completenessText(selected.light) }}
            </span>
          </div>

          <dl class="inspector-properties">
            <div v-for="item in selected.items" :key="item.label">
              <dt>{{ item.label }}</dt>
              <dd>{{ item.value || "-" }}</dd>
            </div>
          </dl>

          <section class="inspector-checks">
            <h3>完整度检查</h3>
            <div v-if="!selected.missing?.length" class="check-card check-card--success">
              <FaSvgIcon icon="ri:checkbox-circle-fill" />
              <div><strong>检查通过</strong><span>该节点的必填信息已完整</span></div>
            </div>
            <div
              v-for="missing in selected.missing"
              :key="missing"
              class="check-card"
              :class="selected.light === 'red' ? 'check-card--danger' : 'check-card--warning'"
            >
              <FaSvgIcon
                :icon="selected.light === 'red' ? 'ri:close-circle-fill' : 'ri:error-warning-fill'"
              />
              <div>
                <strong>{{ selected.light === "red" ? "阻塞发布" : "待完善" }}</strong
                ><span>{{ missing }}</span>
              </div>
              <ElButton
                v-if="selected.fixTarget"
                class="check-card__fix"
                link
                type="primary"
                @click="emit('fix-node', selected.fixTarget)"
              >
                去修复
              </ElButton>
            </div>
          </section>

          <div v-if="selectedActions.length" class="inspector-actions">
            <ElButton
              v-for="action in selectedActions"
              :key="action.label"
              :type="action.type"
              :plain="action.type === 'danger'"
              @click="action.run"
            >
              {{ action.label }}
            </ElButton>
          </div>
        </template>
        <div v-else class="inspector-empty">
          <FaSvgIcon icon="ri:cursor-line" />
          <strong>选择一个节点</strong>
          <span>查看实体、字段、指标或 JOIN 的详细信息</span>
        </div>
      </aside>
    </div>

    <footer v-if="tree" class="workbench-statusbar">
      <span><FaSvgIcon icon="ri:database-2-line" />{{ stats.entities }} 个实体</span>
      <span><FaSvgIcon icon="ri:price-tag-3-line" />{{ stats.fields }} 个字段</span>
      <span><FaSvgIcon icon="ri:bar-chart-box-line" />{{ stats.indicators }} 个指标</span>
      <span><FaSvgIcon icon="ri:share-line" />{{ stats.joins }} 条 JOIN</span>
      <span :class="{ 'statusbar-warning': stats.blockers }"
        ><FaSvgIcon icon="ri:error-warning-line" />{{ stats.blockers }} 项阻塞</span
      >
      <span class="workbench-statusbar__saved"
        ><FaSvgIcon icon="ri:information-line" />布局调整仅在当前页面生效</span
      >
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { VueFlow } from "@vue-flow/core";
import { Background } from "@vue-flow/background";
import { Controls } from "@vue-flow/controls";
import { MiniMap } from "@vue-flow/minimap";
import type { DomainTable } from "@/api/module_semantic/domain";
import type { ModelTree, SemModel, TreeEntityNode, TreeJoin } from "@/api/module_semantic/model";
import EntityGraphNode from "./EntityGraphNode.vue";
import { buildEntityGraph, calculateModelCompleteness, collectWorkbenchStats } from "./workbench";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";
import "@vue-flow/controls/dist/style.css";
import "@vue-flow/minimap/dist/style.css";

interface TreeNodeLike {
  key: string;
  label: string;
  sub?: string;
  icon: string;
  color?: string;
  status?: number;
  light?: "green" | "yellow" | "red";
  children?: TreeNodeLike[];
}

interface SelectedLike {
  title: string;
  nodeType: string;
  items: { label: string; value?: string | number | null }[];
  light?: "green" | "yellow" | "red";
  missing?: string[];
  fixTarget?: { type: "entity" | "dimension" | "indicator"; id: number };
}

interface SelectedAction {
  label: string;
  type: "primary" | "danger";
  run: () => void;
}

const props = withDefaults(
  defineProps<{
    tree?: ModelTree;
    models: SemModel[];
    domains: DomainTable[];
    currentModelId?: number;
    filterDomainId?: number;
    treeLoading: boolean;
    publishing: boolean;
    treeData: TreeNodeLike[];
    expandedKeys: string[];
    selected?: SelectedLike;
    selectedActions?: SelectedAction[];
    canCreate: boolean;
    canUpdate: boolean;
    canDelete: boolean;
    /** module_semantic:model:grant —— 授权是改访问控制，与 canUpdate 分开 */
    canGrant: boolean;
  }>(),
  {
    tree: undefined,
    currentModelId: undefined,
    filterDomainId: undefined,
    selected: undefined,
    selectedActions: () => [],
  }
);

const emit = defineEmits<{
  (event: "select-model", id: number): void;
  (event: "domain-change", id?: number): void;
  (event: "create-model"): void;
  (event: "edit-model"): void;
  (event: "delete-model"): void;
  (event: "add-member"): void;
  (event: "grants"): void;
  (event: "publish"): void;
  (event: "releases"): void;
  (event: "refresh"): void;
  (event: "tree-node-click", node: any): void;
  (event: "graph-entity-click", entity: TreeEntityNode): void;
  (event: "graph-join-click", join: TreeJoin): void;
  (event: "fix-node", target: { type: "entity" | "dimension" | "indicator"; id: number }): void;
}>();

const modelSearch = ref("");
const viewMode = ref<"graph" | "tree">("graph");
const layoutVersion = ref(0);
const inspectorOpen = ref(false);
const isFullscreen = ref(false);
const flowRef = ref<{ fitView: (options?: Record<string, unknown>) => Promise<boolean> }>();

const filteredModels = computed(() => {
  const keyword = modelSearch.value.trim().toLowerCase();
  if (!keyword) return props.models;
  return props.models.filter((model) =>
    `${model.name} ${model.code}`.toLowerCase().includes(keyword)
  );
});

const stats = computed(() =>
  props.tree
    ? collectWorkbenchStats(props.tree)
    : { entities: 0, fields: 0, dimensions: 0, indicators: 0, knowledge: 0, joins: 0, blockers: 0 }
);
const completeness = computed(() => (props.tree ? calculateModelCompleteness(props.tree) : 0));
const completenessColor = computed(() =>
  completeness.value >= 90 ? "#16a34a" : completeness.value >= 65 ? "#f59e0b" : "#ef4444"
);
const graph = computed(() =>
  props.tree ? buildEntityGraph(props.tree) : { nodes: [], edges: [] }
);

const statusMeta = computed(() => {
  const map = {
    0: { type: "info" as const, text: "草稿" },
    1: { type: "warning" as const, text: "待审" },
    2: { type: "success" as const, text: "已发布" },
    3: { type: "danger" as const, text: "已归档" },
  };
  return map[props.tree?.model.status as keyof typeof map] ?? map[0];
});

const assetLibrary = computed(() => [
  {
    label: "实体",
    icon: "ri:database-2-line",
    count: stats.value.entities,
    action: props.canUpdate ? () => emit("add-member") : undefined,
  },
  { label: "指标", icon: "ri:bar-chart-box-line", count: stats.value.indicators },
  { label: "维度", icon: "ri:node-tree", count: stats.value.dimensions },
  { label: "知识", icon: "ri:book-2-line", count: stats.value.knowledge },
]);

const inspectorKind = computed(() => {
  const labels: Record<string, string> = {
    model: "模型详情",
    entity: "实体详情",
    field: "字段详情",
    dimension: "维度详情",
    indicator: "指标详情",
    knowledge: "知识详情",
    join: "关系详情",
  };
  return labels[props.selected?.nodeType ?? ""] ?? "节点详情";
});

function statusText(status: number) {
  return (
    ({ 0: "草稿", 1: "待审", 2: "已发布", 3: "已归档" } as Record<number, string>)[status] ?? "未知"
  );
}

function statusLight(status: number) {
  if (status === 2) return "asset-light--green";
  if (status === 1) return "asset-light--yellow";
  if (status === 3) return "asset-light--red";
  return "asset-light--neutral";
}

function completenessText(light?: "green" | "yellow" | "red") {
  return (
    ({ green: "完整", yellow: "待完善", red: "有阻塞" } as Record<string, string>)[light ?? ""] ??
    "未检查"
  );
}

function miniMapColor(node: { data?: { entity?: TreeEntityNode } }) {
  const light = node.data?.entity?.completeness?.light;
  return light === "green"
    ? "#22c55e"
    : light === "yellow"
      ? "#f59e0b"
      : light === "red"
        ? "#ef4444"
        : "#94a3b8";
}

function onGraphNodeClick(event: { node: { data: { entity: TreeEntityNode } } }) {
  inspectorOpen.value = true;
  emit("graph-entity-click", event.node.data.entity);
}

function onGraphEdgeClick(event: { edge: { data?: { join?: TreeJoin } } }) {
  if (event.edge.data?.join) {
    inspectorOpen.value = true;
    emit("graph-join-click", event.edge.data.join);
  }
}

function onTreeNodeClick(node: any) {
  inspectorOpen.value = true;
  emit("tree-node-click", node);
}

async function fitGraph() {
  await nextTick();
  if (typeof flowRef.value?.fitView === "function") {
    await flowRef.value.fitView({ padding: 0.18, duration: 280 });
  }
}

async function resetGraphLayout() {
  layoutVersion.value += 1;
  await fitGraph();
}

async function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value;
  // 尺寸变化后重新适配画布,保证节点在新视口居中
  await nextTick();
  await fitGraph();
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && isFullscreen.value) {
    isFullscreen.value = false;
  }
}

onMounted(() => document.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown));
</script>

<style scoped lang="scss">
.semantic-workbench {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) 38px;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.workbench-header {
  display: flex;
  gap: 24px;
  align-items: center;
  justify-content: space-between;
  min-height: 104px;
  padding: 16px 22px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.workbench-header__eyebrow {
  margin-bottom: 5px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.workbench-header__title-row {
  display: flex;
  gap: 8px;
  align-items: center;

  h1 {
    margin: 0;
    font-size: 22px;
    line-height: 1.2;
  }
}

.workbench-header__edit {
  padding: 3px;
}

.workbench-header__identity p {
  max-width: 620px;
  margin: 7px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.workbench-header__actions,
.completeness-ring {
  display: flex;
  align-items: center;
}

.workbench-header__actions {
  flex-shrink: 0;
  gap: 9px;
}

.completeness-ring {
  gap: 9px;
  margin-right: 8px;

  span,
  strong {
    display: block;
  }

  span {
    font-size: 10px;
    color: var(--el-text-color-secondary);
  }

  strong {
    margin-top: 2px;
    font-size: 16px;
  }
}

.workbench-main {
  position: relative;
  display: grid;
  grid-template-columns: 226px minmax(420px, 1fr) 310px;
  min-height: 0;
}

.model-rail,
.model-inspector {
  min-width: 0;
  padding: 14px;
  background: var(--el-bg-color);
}

.model-rail {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--el-border-color-lighter);
}

.model-rail__domain {
  width: 100%;
  margin-top: 9px;
}

.rail-section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 17px 2px 8px;
  font-size: 12px;
  font-weight: 650;
}

.rail-section-heading--assets {
  margin-top: 14px;
}

.model-rail__list {
  flex: 1;
  min-height: 110px;
  max-height: 235px;
}

.model-list-item {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto 8px;
  gap: 7px;
  align-items: center;
  width: 100%;
  padding: 9px;
  margin-bottom: 6px;
  color: var(--el-text-color-primary);
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;

  &:hover {
    background: var(--el-fill-color-light);
  }
}

.model-list-item--active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-5);
}

.model-list-item__text {
  min-width: 0;

  strong,
  small {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    font-size: 12px;
  }

  small {
    margin-top: 2px;
    font-family: ui-monospace, monospace;
    font-size: 9px;
    color: var(--el-text-color-secondary);
  }
}

.model-list-item__version {
  font-size: 10px;
  color: var(--el-text-color-secondary);
}

.asset-library {
  display: grid;
  gap: 4px;

  button {
    display: grid;
    grid-template-columns: 18px 1fr auto 14px;
    gap: 6px;
    align-items: center;
    padding: 8px 9px;
    font-size: 11px;
    color: var(--el-text-color-regular);
    text-align: left;
    cursor: pointer;
    background: transparent;
    border: 0;
    border-radius: 5px;

    &:hover {
      background: var(--el-fill-color-light);
    }
  }
}

.asset-library__hint {
  display: flex;
  gap: 9px;
  align-items: center;
  padding: 10px;
  margin-top: 12px;
  font-size: 10px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
  text-align: left;
  cursor: pointer;
  background: var(--el-color-primary-light-9);
  border: 1px dashed var(--el-color-primary-light-5);
  border-radius: 6px;
}

.model-canvas {
  position: relative;
  display: grid;
  grid-template-rows: 48px minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  background: #f7f9fc;
}

.canvas-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.canvas-toolbar__actions {
  display: flex;
  gap: 6px;
}

.canvas-toolbar__tree-meta {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.relationship-canvas,
.structure-tree {
  position: relative;
  min-height: 0;
}

.relationship-canvas {
  background: #f6f8fc;
}

.relationship-canvas--fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: #f6f8fc;
}

.relationship-canvas__exit {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 3;
}

.structure-tree {
  padding: 20px 24px;
  overflow: auto;
  background: var(--el-bg-color);
}

.canvas-empty {
  position: absolute;
  inset: 50% auto auto 50%;
  z-index: 2;
  transform: translate(-50%, -50%);
}

.tree-row {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  font-size: 12px;

  small {
    font-size: 10px;
    color: var(--el-text-color-secondary);
  }
}

.model-inspector {
  position: relative;
  overflow: auto;
  border-left: 1px solid var(--el-border-color-lighter);
}

.inspector-close {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 1;
  display: none;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  background: var(--el-fill-color-light);
  border: 0;
  border-radius: 50%;
}

.inspector-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 13px;
  border-bottom: 1px solid var(--el-border-color-lighter);

  small {
    font-size: 10px;
    color: var(--el-text-color-secondary);
  }

  h2 {
    margin: 4px 0 0;
    font-size: 17px;
  }
}

.inspector-status {
  padding: 3px 7px;
  font-size: 10px;
  font-weight: 600;
  border-radius: 4px;
}

.inspector-status--green {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}

.inspector-status--yellow {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

.inspector-status--red {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.inspector-status--neutral {
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
}

.inspector-properties {
  margin: 12px 0 18px;

  > div {
    display: grid;
    grid-template-columns: 88px minmax(0, 1fr);
    gap: 8px;
    padding: 9px 0;
    border-bottom: 1px solid var(--el-border-color-lighter);
  }

  dt {
    font-size: 11px;
    color: var(--el-text-color-secondary);
  }

  dd {
    min-width: 0;
    margin: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    text-align: right;
    overflow-wrap: anywhere;
  }
}

.inspector-checks h3 {
  margin: 0 0 9px;
  font-size: 12px;
}

.check-card {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  padding: 10px;
  margin-bottom: 8px;
  border: 1px solid;
  border-radius: 6px;

  strong,
  span {
    display: block;
  }

  strong {
    margin-bottom: 3px;
    font-size: 11px;
  }

  span {
    font-size: 10px;
    line-height: 1.4;
    color: var(--el-text-color-secondary);
  }
}

.check-card__fix {
  flex-shrink: 0;
  align-self: center;
  margin-left: auto;
  font-size: 11px;
}

.check-card--success {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
  border-color: var(--el-color-success-light-5);
}

.check-card--warning {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
  border-color: var(--el-color-warning-light-5);
}

.check-card--danger {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
  border-color: var(--el-color-danger-light-5);
}

.inspector-actions {
  display: grid;
  gap: 8px;
  margin-top: 18px;
}

.inspector-empty {
  display: flex;
  flex-direction: column;
  gap: 7px;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--el-text-color-placeholder);
  text-align: center;

  > svg {
    font-size: 28px;
  }

  strong {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  span {
    max-width: 190px;
    font-size: 10px;
    line-height: 1.5;
  }
}

.workbench-statusbar {
  display: flex;
  gap: 24px;
  align-items: center;
  padding: 0 16px;
  font-size: 10px;
  color: var(--el-text-color-secondary);
  border-top: 1px solid var(--el-border-color-lighter);

  span {
    display: inline-flex;
    gap: 5px;
    align-items: center;
  }
}

.statusbar-warning {
  color: var(--el-color-warning);
}

.workbench-statusbar__saved {
  margin-left: auto;
  color: var(--el-text-color-secondary);
}

.asset-light {
  display: inline-block;
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.asset-light--green {
  background: var(--el-color-success);
}

.asset-light--yellow {
  background: var(--el-color-warning);
}

.asset-light--red {
  background: var(--el-color-danger);
}

.asset-light--neutral {
  background: var(--el-text-color-placeholder);
}

:deep(.vue-flow__node-entity.selected .entity-node) {
  border-color: var(--el-color-primary);
  box-shadow:
    0 0 0 2px var(--el-color-primary-light-8),
    0 10px 28px rgb(37 99 235 / 14%);
}

:deep(.vue-flow__edge.selected .vue-flow__edge-path) {
  stroke: var(--el-color-primary);
  stroke-width: 2;
}

@media (width <= 1280px) {
  .workbench-main {
    grid-template-columns: 205px minmax(360px, 1fr) 280px;
  }

  .workbench-header__actions {
    .completeness-ring {
      display: none;
    }
  }
}

@media (width <= 1024px) {
  .workbench-main {
    grid-template-columns: 190px minmax(360px, 1fr);
  }

  .model-inspector {
    position: absolute;
    inset: 0 0 0 auto;
    z-index: 8;
    display: none;
    width: min(310px, calc(100% - 190px));
    background: var(--el-bg-color);
    box-shadow: -12px 0 28px rgb(15 23 42 / 12%);
  }

  .model-inspector--open {
    display: block;
  }

  .inspector-close {
    display: grid;
  }

  .inspector-heading {
    padding-right: 34px;
  }

  .workbench-header__actions > :nth-child(3) {
    display: none;
  }
}
</style>
