<!-- 语义模型编排工作台：关系画布 + 结构树 + 节点检查器 -->
<template>
  <div class="model-page fa-full-height">
    <SemanticModelWorkbench
      :tree="tree"
      :models="models"
      :domains="domains"
      :current-model-id="currentModelId"
      :filter-domain-id="filterDomainId"
      :tree-loading="treeLoading"
      :publishing="publishing"
      :tree-data="treeData"
      :expanded-keys="expandedKeys"
      :selected="selected"
      :selected-actions="selectedActions"
      :can-create="hasAuth('module_semantic:model:create')"
      :can-update="hasAuth('module_semantic:model:update')"
      :can-delete="hasAuth('module_semantic:model:delete')"
      :can-grant="hasAuth('module_semantic:model:grant')"
      @select-model="selectModel"
      @domain-change="handleDomainChange"
      @create-model="openModelForm()"
      @edit-model="tree && openModelForm(tree.model)"
      @delete-model="doDeleteModel"
      @add-member="openAddMember"
      @grants="openGrants"
      @publish="doPublish"
      @releases="openReleases"
      @refresh="validateModel"
      @tree-node-click="onNodeClick"
      @graph-entity-click="selectGraphEntity"
      @graph-join-click="selectGraphJoin"
      @fix-node="goFix"
    />

    <!-- 新建/编辑模型对话框 -->
    <FaDialog
      v-model="modelFormVisible"
      :title="modelForm.id ? '编辑语义模型' : '新建语义模型'"
      width="560px"
      :form-mode="modelForm.id ? 'update' : 'create'"
      :confirm-loading="modelSaving"
      @cancel="modelFormVisible = false"
      @confirm="doSaveModel"
    >
      <ElForm label-width="110px">
        <ElFormItem label="模型编码" required>
          <ElInput
            v-model="modelForm.code"
            :disabled="!!modelForm.id"
            placeholder="如 trade_analysis"
            maxlength="50"
          />
        </ElFormItem>
        <ElFormItem label="模型名称" required>
          <ElInput v-model="modelForm.name" placeholder="如 交易分析" maxlength="100" />
        </ElFormItem>
        <ElFormItem label="业务域">
          <ElSelect v-model="modelForm.domain_id" clearable placeholder="归属业务域" class="w-full">
            <ElOption v-for="d in domains" :key="d.id" :label="d.name" :value="d.id!" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="默认时间粒度">
          <ElSelect v-model="modelForm.default_time_grain" clearable class="w-full">
            <ElOption label="日" value="day" />
            <ElOption label="周" value="week" />
            <ElOption label="月" value="month" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="场景说明">
          <ElInput
            v-model="modelForm.description"
            type="textarea"
            :rows="3"
            placeholder="分析场景说明(发布后会进 agent 上下文开头)"
            maxlength="500"
          />
        </ElFormItem>
        <!-- 可见性：此前不在任何 schema 里，只能建完再去改库。它决定"谁能通过 MCP
             问到这个模型"，属于建模时就该定的事 -->
        <ElFormItem v-if="canGrant" label="可见性">
          <ElRadioGroup v-model="modelForm.visibility">
            <ElRadio value="public">所有调用方</ElRadio>
            <ElRadio value="restricted">限制可见</ElRadio>
          </ElRadioGroup>
          <div class="model-form-hint">
            <template v-if="modelForm.visibility === 'restricted'">
              仅<strong>模型负责人</strong>、<strong>被授权用户</strong>与超管可经 MCP 访问。
              保存后请到「授权管理」加人 ——
              <strong>未绑用户的 token 会一并被挡在外面</strong>。
            </template>
            <template v-else>
              所有通过鉴权的 MCP 调用方都能检索到它并取数（默认）
            </template>
          </div>
        </ElFormItem>
      </ElForm>
    </FaDialog>

    <!-- 授权管理：MCP 用户隔离的管理面 -->
    <FaDialog
      v-model="grantVisible"
      title="授权管理"
      width="640px"
      form-mode="detail"
      confirm-text="关闭"
      @confirm="grantVisible = false"
    >
      <ElAlert
        v-if="tree && tree.model.visibility !== 'restricted'"
        type="info"
        :closable="false"
        show-icon
        class="mb-3"
      >
        <template #title>当前模型为「所有调用方可见」，授权名单暂不生效</template>
        <div class="text-xs">
          可以先把名单配好，再到「编辑」里把可见性改为<strong>限制可见</strong> ——
          这样切换那一刻不会出现所有人都被挡在外面。
        </div>
      </ElAlert>

      <div class="grant-add">
        <ElSelect
          v-model="grantPick"
          multiple
          filterable
          remote
          reserve-keyword
          :remote-method="searchUsers"
          :loading="userLoading"
          placeholder="搜索用户名/姓名后选择"
          class="grant-add__select"
        >
          <ElOption
            v-for="u in userOptions"
            :key="u.id"
            :label="`${u.name || u.username}（${u.username}）`"
            :value="u.id!"
          />
        </ElSelect>
        <ElButton
          type="primary"
          :loading="grantSaving"
          :disabled="!grantPick.length"
          @click="doAddGrant"
        >
          授权
        </ElButton>
      </div>

      <ElTable v-loading="grantLoading" :data="grants" size="small" border class="mt-3">
        <ElTableColumn prop="username" label="用户名" width="150" />
        <ElTableColumn prop="user_name" label="姓名" min-width="120" />
        <ElTableColumn prop="created_time" label="授权时间" width="168" />
        <ElTableColumn label="操作" width="90" align="center">
          <template #default="{ row }">
            <ElButton size="small" link type="danger" @click="doRevokeGrant(row)">收回</ElButton>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty :image-size="60" description="尚未授权任何用户" />
        </template>
      </ElTable>
      <!-- owner 免授权是判定层的规则(否则建模的人看不到自己的模型)，得说出来，
           否则运维会疑惑"名单是空的为什么负责人能看到" -->
      <div class="model-form-hint mt-2">
        模型负责人与超管无需出现在此名单即可访问。
      </div>
    </FaDialog>

    <!-- 发布版本对话框 -->
    <FaDialog
      v-model="releasesVisible"
      title="发布版本"
      width="680px"
      form-mode="detail"
      confirm-text="关闭"
      @confirm="releasesVisible = false"
    >
      <ElTable v-if="releases.length" :data="releases" size="small" border max-height="50vh">
        <ElTableColumn label="版本" width="70" align="center">
          <template #default="{ row }">v{{ row.version }}</template>
        </ElTableColumn>
        <ElTableColumn label="节点统计">
          <template #default="{ row }">{{ statsText(row.node_stats) }}</template>
        </ElTableColumn>
        <ElTableColumn prop="created_time" label="发布时间" width="160" />
        <ElTableColumn label="操作" width="100" align="center">
          <template #default="{ row }">
            <ElButton size="small" link type="primary" @click="viewSnapshot(row.id)"
              >查看快照</ElButton
            >
          </template>
        </ElTableColumn>
      </ElTable>
      <ElEmpty v-else description="尚未发布过版本" :image-size="60" />
    </FaDialog>

    <!-- 快照查看对话框 -->
    <FaDialog
      v-model="snapshotVisible"
      :title="`Release v${snapshotVersion} · 语义上下文`"
      width="780px"
      form-mode="detail"
      confirm-text="关闭"
      @confirm="snapshotVisible = false"
    >
      <ElScrollbar max-height="60vh">
        <pre class="snapshot-pre">{{ snapshotText }}</pre>
      </ElScrollbar>
    </FaDialog>

    <!-- 添加实体成员对话框 -->
    <FaDialog
      v-model="memberFormVisible"
      title="添加实体到模型"
      width="520px"
      form-mode="create"
      :confirm-loading="memberSaving"
      @cancel="memberFormVisible = false"
      @confirm="doAddMember"
    >
      <ElForm label-width="100px">
        <ElFormItem label="实体" required>
          <EntitySelect v-model="memberForm.entity_id" :exclude-ids="existingMemberIds" />
        </ElFormItem>
        <ElFormItem label="角色">
          <ElRadioGroup v-model="memberForm.role">
            <ElRadioButton value="primary">主事实</ElRadioButton>
            <ElRadioButton value="related">关联</ElRadioButton>
          </ElRadioGroup>
        </ElFormItem>
        <ElFormItem label="排序">
          <ElInputNumber v-model="memberForm.sort" :min="0" :max="999" />
        </ElFormItem>
      </ElForm>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuth } from "@/hooks/core/useAuth";
import ModelAPI, {
  type ModelGrant,
  type ModelRelease,
  type ModelTree,
  type ModelVisibility,
  type SemModel,
  type TreeEntityNode,
  type TreeJoin,
} from "@/api/module_semantic/model";
import DomainAPI, { type DomainTable } from "@/api/module_semantic/domain";
import UserAPI, { type UserInfo } from "@/api/module_system/user";
import SemanticModelWorkbench from "./SemanticModelWorkbench.vue";
import EntitySelect from "../_shared/EntitySelect.vue";
import { useNavigateToNode } from "../_shared/useNavigateToNode";
import {
  canApplySelectionResponse,
  ENTITY_TYPE_MAP,
  FIELD_ROLE_MAP,
  KNOWLEDGE_TYPE_MAP,
  REL_MAP,
  resolveFilteredModelId,
} from "./workbench";

defineOptions({ name: "SemanticModel", inheritAttrs: false });

const { hasAuth } = useAuth();
const { goEntity, goDimension, goIndicator } = useNavigateToNode();

// ---- 左侧: 域 + 模型列表 ----
const domains = ref<DomainTable[]>([]);
const models = ref<SemModel[]>([]);
const filterDomainId = ref<number>();
const currentModelId = ref<number>();
let latestModelListRequest = 0;

async function loadDomains() {
  const res = await DomainAPI.listDomain({ page_no: 1, page_size: 100 });
  domains.value = res.data.data?.items || [];
}
async function loadModels(domainId = filterDomainId.value): Promise<SemModel[] | undefined> {
  const requestId = ++latestModelListRequest;
  const res = await ModelAPI.listModel({
    page_no: 1,
    page_size: 100,
    domain_id: domainId,
  });
  if (
    !canApplySelectionResponse(requestId, latestModelListRequest, domainId, filterDomainId.value)
  ) {
    return;
  }
  const nextModels = res.data.data?.items || [];
  models.value = nextModels;
  return nextModels;
}
async function handleDomainChange(id?: number) {
  filterDomainId.value = id;
  const nextModels = await loadModels(id);
  if (!nextModels || filterDomainId.value !== id) return;
  const nextModelId = resolveFilteredModelId(nextModels, currentModelId.value);
  if (nextModelId === currentModelId.value) return;
  if (nextModelId) {
    selectModel(nextModelId);
  } else {
    latestTreeRequest += 1;
    currentModelId.value = undefined;
    tree.value = undefined;
    selected.value = undefined;
    treeLoading.value = false;
  }
}

// ---- 树 ----
const tree = ref<ModelTree>();
const treeLoading = ref(false);
let latestTreeRequest = 0;

interface TreeNodeData {
  key: string;
  label: string;
  sub?: string;
  icon: string;
  color?: string;
  status?: number;
  light?: "green" | "yellow" | "red";
  missing?: string[];
  nodeType: string;
  payload?: unknown;
  children?: TreeNodeData[];
}

const treeData = computed<TreeNodeData[]>(() => {
  const t = tree.value;
  if (!t) return [];
  const children: TreeNodeData[] = [];

  for (const kn of t.model_knowledge) {
    children.push({
      key: `mkn-${kn.id}`,
      label: kn.summary,
      sub: KNOWLEDGE_TYPE_MAP[kn.knowledge_type] ?? kn.knowledge_type,
      icon: "ri:file-text-line",
      color: "#e6a23c",
      status: kn.status,
      light: kn.completeness?.light,
      missing: kn.completeness?.missing,
      nodeType: "knowledge",
      payload: kn,
    });
  }

  for (const e of t.entities) {
    children.push(entityNode(e));
  }

  if (t.joins.length) {
    children.push({
      key: "joins",
      label: `关系 (${t.joins.length})`,
      icon: "ri:share-line",
      color: "#909399",
      nodeType: "joins",
      children: t.joins.map((j) => ({
        key: `join-${j.id}`,
        label: `${j.from_entity_code ?? j.from_entity_id} → ${j.to_entity_code ?? j.to_entity_id}`,
        sub: `${REL_MAP[j.relationship_type] ?? j.relationship_type} · ${j.on_expr}`,
        icon: "ri:links-line",
        status: j.status,
        light: j.completeness?.light,
        missing: j.completeness?.missing,
        nodeType: "join",
        payload: j,
      })),
    });
  }

  return [
    {
      key: "model",
      label: t.model.name,
      sub: `${t.model.code} · v${t.model.version}`,
      icon: "ri:organization-chart",
      color: "var(--el-color-primary)",
      status: t.model.status,
      light: t.model_completeness?.light,
      missing: t.model_completeness?.missing,
      nodeType: "model",
      payload: t.model,
      children,
    },
  ];
});

const expandedKeys = computed(() => {
  const keys = ["model", "joins"];
  for (const e of tree.value?.entities ?? []) keys.push(`entity-${e.id}`);
  return keys;
});

function entityNode(e: TreeEntityNode): TreeNodeData {
  const groups: TreeNodeData[] = [];
  if (e.fields.length) {
    groups.push({
      key: `e${e.id}-fields`,
      label: `字段 (${e.fields.length})`,
      icon: "ri:list-check-2",
      nodeType: "group",
      children: e.fields.map((f) => ({
        key: `field-${f.id}`,
        label: `${f.name} ${f.code}`,
        sub: FIELD_ROLE_MAP[f.field_role ?? ""] ?? f.field_role,
        icon: "ri:price-tag-3-line",
        light: f.completeness?.light,
        missing: f.completeness?.missing,
        nodeType: "field",
        payload: f,
      })),
    });
  }
  if (e.dimensions.length) {
    groups.push({
      key: `e${e.id}-dims`,
      label: `维度 (${e.dimensions.length})`,
      icon: "ri:node-tree",
      nodeType: "group",
      children: e.dimensions.map((d) => ({
        key: `dim-${d.id}`,
        label: d.name,
        sub: d.bind_field_code ? `绑 ${d.bind_field_code}` : undefined,
        icon: "ri:node-tree",
        status: d.status,
        light: d.completeness?.light,
        missing: d.completeness?.missing,
        nodeType: "dimension",
        payload: d,
      })),
    });
  }
  if (e.indicators.length) {
    groups.push({
      key: `e${e.id}-inds`,
      label: `指标 (${e.indicators.length})`,
      icon: "ri:bar-chart-box-line",
      nodeType: "group",
      children: e.indicators.map((i) => ({
        key: `ind-${i.id}`,
        label: i.name,
        sub: i.code,
        icon: "ri:bar-chart-box-line",
        status: i.status,
        light: i.completeness?.light,
        missing: i.completeness?.missing,
        nodeType: "indicator",
        payload: i,
      })),
    });
  }
  if (e.knowledge.length) {
    groups.push({
      key: `e${e.id}-kn`,
      label: `知识 (${e.knowledge.length})`,
      icon: "ri:book-2-line",
      nodeType: "group",
      children: e.knowledge.map((k) => ({
        key: `kn-${k.id}`,
        label: k.summary,
        sub: KNOWLEDGE_TYPE_MAP[k.knowledge_type] ?? k.knowledge_type,
        icon: "ri:file-text-line",
        color: "#e6a23c",
        status: k.status,
        light: k.completeness?.light,
        missing: k.completeness?.missing,
        nodeType: "knowledge",
        payload: k,
      })),
    });
  }
  const physical = e.physical_table ? `${e.physical_database}.${e.physical_table}` : undefined;
  return {
    key: `entity-${e.id}`,
    label: e.name,
    sub: `${ENTITY_TYPE_MAP[e.entity_type ?? ""] ?? e.entity_type}${e.role === "primary" ? " · 主事实" : ""}${physical ? ` · ${physical}` : ""}`,
    icon: "ri:database-2-line",
    color: "#67c23a",
    status: e.status,
    light: e.completeness?.light,
    missing: e.completeness?.missing,
    nodeType: "entity",
    payload: e,
    children: groups,
  };
}

async function loadTree(id: number): Promise<ModelTree | undefined> {
  const requestId = ++latestTreeRequest;
  treeLoading.value = true;
  try {
    const res = await ModelAPI.tree(id);
    if (!canApplySelectionResponse(requestId, latestTreeRequest, id, currentModelId.value)) {
      return;
    }
    tree.value = res.data.data;
    const entities = tree.value?.entities ?? [];
    const defaultEntity = entities.find((entity) => entity.role === "primary") ?? entities[0];
    if (defaultEntity) {
      selectGraphEntity(defaultEntity);
    } else if (tree.value) {
      onNodeClick(treeData.value[0]!);
    } else {
      selected.value = undefined;
    }
    return tree.value;
  } finally {
    if (requestId === latestTreeRequest) treeLoading.value = false;
  }
}
function selectModel(id: number) {
  currentModelId.value = id;
  loadTree(id);
}

async function validateModel() {
  const modelId = currentModelId.value;
  if (!modelId) return;
  const validatedTree = await loadTree(modelId);
  if (!validatedTree || currentModelId.value !== modelId) return;
  const blockerCount = validatedTree.publish_blockers.length;
  if (blockerCount) {
    ElMessage.warning(`校验完成，还有 ${blockerCount} 项阻塞问题`);
  } else {
    ElMessage.success("校验完成，模型可以发布");
  }
}

// ---- 右侧详情 ----
interface DetailItem {
  label: string;
  value?: string | number | null;
}
// 去修复跳转目标: 仅实体/维度/指标有独立管理页(接了 focus 高亮 + B 阶段选择器);
// 字段/知识/JOIN 无独立管理页,不给跳转目标(在管理页里随所属实体一并维护)。
type FixTargetType = "entity" | "dimension" | "indicator";
interface FixTarget {
  type: FixTargetType;
  id: number;
}
interface SelectedNode {
  title: string;
  icon: string;
  items: DetailItem[];
  nodeType: string;
  payload?: unknown;
  light?: "green" | "yellow" | "red";
  missing?: string[];
  fixTarget?: FixTarget;
}
const selected = ref<SelectedNode>();

function resolveFixTarget(data: TreeNodeData): FixTarget | undefined {
  const id = (data.payload as { id?: number } | undefined)?.id;
  if (typeof id !== "number") return undefined;
  if (data.nodeType === "entity") return { type: "entity", id };
  if (data.nodeType === "dimension") return { type: "dimension", id };
  if (data.nodeType === "indicator") return { type: "indicator", id };
  return undefined;
}

function goFix(target: FixTarget) {
  if (target.type === "entity") goEntity(target.id);
  else if (target.type === "dimension") goDimension(target.id);
  else goIndicator(target.id);
}

function onNodeClick(data: TreeNodeData) {
  const t = tree.value;
  if (!t) return;
  switch (data.nodeType) {
    case "model":
      selected.value = {
        title: "语义模型",
        icon: "ri:organization-chart",
        nodeType: "model",
        payload: t.model,
        items: [
          { label: "编码", value: t.model.code },
          { label: "业务域", value: t.domain_name },
          { label: "版本", value: `v${t.model.version}` },
          { label: "默认时间粒度", value: t.model.default_time_grain },
          { label: "说明", value: t.model.description },
        ],
      };
      break;
    case "entity": {
      const e = data.payload as TreeEntityNode;
      selected.value = {
        title: `实体 · ${e.name}`,
        icon: "ri:database-2-line",
        nodeType: "entity",
        payload: e,
        items: [
          { label: "编码", value: e.code },
          { label: "类型", value: ENTITY_TYPE_MAP[e.entity_type ?? ""] ?? e.entity_type },
          { label: "成员角色", value: e.role === "primary" ? "主事实" : "关联" },
          { label: "粒度", value: e.grain },
          {
            label: "物理表",
            value: e.physical_table ? `${e.physical_database}.${e.physical_table}` : "未绑定",
          },
          { label: "描述", value: e.description },
        ],
      };
      break;
    }
    case "field": {
      const f = data.payload as {
        name: string;
        code: string;
        field_role?: string;
        data_type?: string;
        physical_column?: string;
      };
      selected.value = {
        title: `字段 · ${f.name}`,
        icon: "ri:price-tag-3-line",
        nodeType: "field",
        items: [
          { label: "编码", value: f.code },
          { label: "角色", value: FIELD_ROLE_MAP[f.field_role ?? ""] ?? f.field_role },
          { label: "语义类型", value: f.data_type },
          { label: "物理列", value: f.physical_column ?? "表达式/未绑定" },
        ],
      };
      break;
    }
    case "dimension": {
      const d = data.payload as { name: string; code: string; bind_field_code?: string };
      selected.value = {
        title: `维度 · ${d.name}`,
        icon: "ri:node-tree",
        nodeType: "dimension",
        items: [
          { label: "编码", value: d.code },
          { label: "绑定字段", value: d.bind_field_code },
        ],
      };
      break;
    }
    case "indicator": {
      const i = data.payload as { name: string; code: string; indicator_type: string };
      selected.value = {
        title: `指标 · ${i.name}`,
        icon: "ri:bar-chart-box-line",
        nodeType: "indicator",
        items: [
          { label: "编码", value: i.code },
          { label: "类型", value: i.indicator_type },
        ],
      };
      break;
    }
    case "knowledge": {
      const k = data.payload as {
        summary: string;
        key: string;
        knowledge_type: string;
        usage_mode: string;
      };
      selected.value = {
        title: "知识条目",
        icon: "ri:file-text-line",
        nodeType: "knowledge",
        items: [
          { label: "摘要", value: k.summary },
          { label: "键", value: k.key },
          { label: "类型", value: KNOWLEDGE_TYPE_MAP[k.knowledge_type] ?? k.knowledge_type },
          { label: "使用模式", value: k.usage_mode },
        ],
      };
      break;
    }
    case "join": {
      const j = data.payload as { on_expr: string; relationship_type: string; join_type: string };
      selected.value = {
        title: "JOIN 关系",
        icon: "ri:links-line",
        nodeType: "join",
        items: [
          { label: "条件", value: j.on_expr },
          { label: "关系", value: REL_MAP[j.relationship_type] ?? j.relationship_type },
          { label: "类型", value: j.join_type },
        ],
      };
      break;
    }
    default:
      selected.value = undefined;
  }
  if (selected.value) {
    selected.value.light = data.light;
    selected.value.missing = data.missing;
    selected.value.fixTarget = resolveFixTarget(data);
  }
}

function selectGraphEntity(entity: TreeEntityNode) {
  onNodeClick(entityNode(entity));
}

function selectGraphJoin(join: TreeJoin) {
  onNodeClick({
    key: `join-${join.id}`,
    label: `${join.from_entity_code ?? join.from_entity_id} → ${join.to_entity_code ?? join.to_entity_id}`,
    sub: `${REL_MAP[join.relationship_type] ?? join.relationship_type} · ${join.on_expr}`,
    icon: "ri:links-line",
    status: join.status,
    light: join.completeness?.light,
    missing: join.completeness?.missing,
    nodeType: "join",
    payload: join,
  });
}

// 节点级操作
const selectedActions = computed(() => {
  const acts: { label: string; type: "primary" | "danger"; run: () => void }[] = [];
  const s = selected.value;
  if (!s || !hasAuth("module_semantic:model:update")) return acts;
  if (s.nodeType === "entity") {
    const e = s.payload as TreeEntityNode;
    if (e.role !== "primary") {
      acts.push({
        label: "设为主事实",
        type: "primary",
        run: () => doUpdateMemberRole(e.member_id, "primary"),
      });
    }
    acts.push({ label: "从模型移除", type: "danger", run: () => doRemoveMember(e) });
  }
  return acts;
});

// ---- 发布 ----
const publishing = ref(false);
async function doPublish() {
  if (!currentModelId.value || !tree.value) return;
  await ElMessageBox.confirm(
    `将「${tree.value.model.name}」当前树编译为 Release v${tree.value.model.version + 1} 语义上下文(只含已发布节点)?`,
    "确认发布",
    { type: "info" }
  );
  publishing.value = true;
  try {
    const res = await ModelAPI.publish(currentModelId.value);
    ElMessage.success(res.data.msg || `发布成功: v${res.data.data?.version}`);
    await loadModels();
    await loadTree(currentModelId.value);
  } catch (e: any) {
    ElMessage.error(e?.message || "发布失败");
  } finally {
    publishing.value = false;
  }
}

// ---- 版本/快照 ----
const releasesVisible = ref(false);
const releases = ref<ModelRelease[]>([]);
async function openReleases() {
  if (!currentModelId.value) return;
  const res = await ModelAPI.listRelease(currentModelId.value);
  releases.value = res.data.data || [];
  releasesVisible.value = true;
}
function statsText(stats?: string): string {
  if (!stats) return "-";
  try {
    const s = JSON.parse(stats);
    return `实体${s.entities} · 字段${s.fields} · 维度${s.dimensions} · 指标${s.indicators} · 关系${s.joins} · 知识${s.knowledge}`;
  } catch {
    return "-";
  }
}
const snapshotVisible = ref(false);
const snapshotText = ref("");
const snapshotVersion = ref(0);
async function viewSnapshot(id: number) {
  const res = await ModelAPI.releaseDetail(id);
  const d = res.data.data;
  snapshotVersion.value = d?.version ?? 0;
  try {
    snapshotText.value = JSON.stringify(JSON.parse(d?.snapshot ?? "{}"), null, 2);
  } catch {
    snapshotText.value = d?.snapshot ?? "";
  }
  snapshotVisible.value = true;
}

// ---- 模型 CRUD ----
const modelFormVisible = ref(false);
const modelSaving = ref(false);
const modelForm = ref<{
  id?: number;
  code: string;
  name: string;
  domain_id?: number;
  default_time_grain?: string;
  description?: string;
  visibility: ModelVisibility;
}>({
  code: "",
  name: "",
  visibility: "public",
});
function openModelForm(m?: SemModel) {
  modelForm.value = m
    ? {
        id: m.id,
        code: m.code,
        name: m.name,
        domain_id: m.domain_id,
        default_time_grain: m.default_time_grain,
        description: m.description,
        // 后端默认 public；老数据可能没有该字段，兜底同默认值
        visibility: m.visibility ?? "public",
      }
    : { code: "", name: "", domain_id: filterDomainId.value, visibility: "public" };
  modelFormVisible.value = true;
}
async function doSaveModel() {
  const f = modelForm.value;
  if (!f.code?.trim() || !f.name?.trim()) {
    ElMessage.warning("编码/名称必填");
    return;
  }
  modelSaving.value = true;
  try {
    if (f.id) {
      await ModelAPI.updateModel(f.id, f);
    } else {
      const res = await ModelAPI.createModel(f);
      currentModelId.value = res.data.data?.id;
    }
    ElMessage.success("保存成功");
    modelFormVisible.value = false;
    await loadModels();
    if (currentModelId.value) await loadTree(currentModelId.value);
  } catch (e: any) {
    ElMessage.error(e?.message || "保存失败");
  } finally {
    modelSaving.value = false;
  }
}
async function doDeleteModel() {
  if (!tree.value) return;
  await ElMessageBox.confirm(
    `删除语义模型「${tree.value.model.name}」?成员关系与发布快照会一并删除(实体本身保留)。`,
    "确认删除",
    { type: "warning" }
  );
  await ModelAPI.deleteModel([tree.value.model.id]);
  ElMessage.success("删除成功");
  tree.value = undefined;
  currentModelId.value = undefined;
  selected.value = undefined;
  await loadModels();
}

// ---- 用户授权（MCP 用户隔离的管理面）----
//
// 判定层(retrieval/visibility.py)早就做好, 但此前 visibility 不在任何 schema 里、
// sem_model_grant 没有任何接口引用 —— 隔离能力只能靠写 SQL 启用。

const canGrant = computed(() => hasAuth("module_semantic:model:grant"));
const grantVisible = ref(false);
const grantLoading = ref(false);
const grantSaving = ref(false);
const grants = ref<ModelGrant[]>([]);
const grantPick = ref<number[]>([]);
const userOptions = ref<UserInfo[]>([]);
const userLoading = ref(false);

async function loadGrants() {
  if (!currentModelId.value) return;
  grantLoading.value = true;
  try {
    const res = await ModelAPI.listGrant(currentModelId.value);
    grants.value = res.data.data ?? [];
  } catch {
    grants.value = []; // 读失败不该让整页崩
  } finally {
    grantLoading.value = false;
  }
}

/** 远程搜用户：全量拉列表在几百人的实例上不可用，且授权是逐个点选的动作 */
async function searchUsers(keyword: string) {
  userLoading.value = true;
  try {
    const res = await UserAPI.listUser({ page_no: 1, page_size: 20, name: keyword || undefined });
    const granted = new Set(grants.value.map((g) => g.user_id));
    // 已授权的从候选里去掉：留着只会让人点一次再看到"没变化"(后端幂等跳过)
    userOptions.value = (res.data.data?.items || []).filter(
      (u) => u.id !== undefined && !granted.has(u.id),
    );
  } catch {
    userOptions.value = [];
  } finally {
    userLoading.value = false;
  }
}

async function openGrants() {
  if (!currentModelId.value) return;
  grantPick.value = [];
  userOptions.value = [];
  grantVisible.value = true;
  await loadGrants();
  await searchUsers(""); // 预填一页，免得打开就是空下拉
}

async function doAddGrant() {
  if (!currentModelId.value || !grantPick.value.length) return;
  grantSaving.value = true;
  try {
    await ModelAPI.addGrant(currentModelId.value, grantPick.value);
    ElMessage.success(`已授权 ${grantPick.value.length} 人`);
    grantPick.value = [];
    await loadGrants();
    await searchUsers("");
  } catch (e: any) {
    ElMessage.error(e?.message || "授权失败");
  } finally {
    grantSaving.value = false;
  }
}

async function doRevokeGrant(row: ModelGrant) {
  if (!currentModelId.value) return;
  try {
    await ElMessageBox.confirm(
      `收回「${row.user_name || row.username}」对本模型的访问权？`
      + `\n若模型为限制可见，收回后该用户经 MCP 立刻问不到它。`,
      "确认收回",
      { type: "warning", confirmButtonText: "收回", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await ModelAPI.revokeGrant(currentModelId.value, [row.user_id]);
    ElMessage.success("已收回");
    await loadGrants();
    await searchUsers("");
  } catch (e: any) {
    ElMessage.error(e?.message || "收回失败");
  }
}

// ---- 成员管理 ----
const memberFormVisible = ref(false);
const memberSaving = ref(false);
const memberForm = ref<{ entity_id?: number; role: string; sort: number }>({
  role: "related",
  sort: 0,
});
/** 已入模实体在共享下拉中过滤掉,避免重复添加 */
const existingMemberIds = computed(() =>
  (tree.value?.entities ?? []).map((e) => e.id).filter((id): id is number => id != null)
);
function openAddMember() {
  memberForm.value = { role: "related", sort: (tree.value?.entities.length ?? 0) + 1 };
  memberFormVisible.value = true;
}
async function doAddMember() {
  const entityId = memberForm.value.entity_id;
  if (!entityId || !currentModelId.value) {
    ElMessage.warning("请选择实体");
    return;
  }
  memberSaving.value = true;
  try {
    await ModelAPI.addMember(currentModelId.value, {
      entity_id: entityId,
      role: memberForm.value.role,
      sort: memberForm.value.sort,
    });
    ElMessage.success("添加成功");
    memberFormVisible.value = false;
    await loadTree(currentModelId.value);
  } catch (e: any) {
    ElMessage.error(e?.message || "添加失败");
  } finally {
    memberSaving.value = false;
  }
}
async function doUpdateMemberRole(memberId: number, role: string) {
  if (!currentModelId.value) return;
  await ModelAPI.updateMember(memberId, { role });
  ElMessage.success("已设为主事实");
  await loadTree(currentModelId.value);
}
async function doRemoveMember(e: TreeEntityNode) {
  await ElMessageBox.confirm(
    `将实体「${e.name}」从模型移除?(实体本身保留,仅从本模型摘出)`,
    "确认移除",
    { type: "warning" }
  );
  await ModelAPI.removeMember([e.member_id]);
  ElMessage.success("已移除");
  selected.value = undefined;
  if (currentModelId.value) await loadTree(currentModelId.value);
}

onMounted(async () => {
  await loadDomains();
  await loadModels();
  const first = models.value[0];
  if (first?.id) selectModel(first.id);
});
</script>

<style scoped lang="scss">
.model-page {
  min-width: 0;
  min-height: 0;
}

.snapshot-pre {
  padding: 12px;
  font-size: 0.75rem;
  line-height: 1.5;
  word-break: break-all;
  white-space: pre-wrap;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.model-form-hint {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-placeholder);
}

.grant-add {
  display: flex;
  gap: 8px;
  align-items: center;

  &__select {
    flex: 1;
    min-width: 0;
  }
}
</style>
