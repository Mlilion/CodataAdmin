<!-- 上游变更：语义中心 - 上游改表后受影响的语义资产待办 -->
<template>
  <div class="uc-page fa-full-height">
    <ElCard class="fa-table-card">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <span class="font-medium">上游变更</span>
            <ElTag size="small" type="info" effect="plain">来自物理元数据同步</ElTag>
          </div>
          <!-- ★ flex-wrap + 内联宽度: 这排控件挤在一行时 ElSelect 会撑满整行,
               把标签压成竖排(治理页踩过, 见提交 b81142bb) -->
          <div class="flex flex-wrap items-center gap-2">
            <span class="uc-label shrink-0">状态</span>
            <ElSelect
              v-model="statusFilter" size="small" style="width: 110px; flex: none"
              @change="load"
            >
              <ElOption value="pending" label="待处理" />
              <ElOption value="resolved" label="已处理" />
              <ElOption value="ignored" label="已忽略" />
            </ElSelect>
            <span class="uc-label shrink-0">影响</span>
            <ElSelect
              v-model="levelFilter" size="small" style="width: 130px; flex: none"
              clearable placeholder="全部" @change="load"
            >
              <ElOption value="bound" label="绑定失效" />
              <ElOption value="sql_ref" label="口径 SQL" />
              <ElOption value="table_only" label="仅表相关" />
            </ElSelect>
            <ElButton size="small" :loading="loading" @click="load">刷新</ElButton>
          </div>
        </div>
      </template>

      <ElAlert type="info" :closable="false" show-icon class="mb-3">
        <template #title>上游改表后，哪些语义资产受影响</template>
        <div class="text-xs leading-relaxed">
          只列<strong>碰到语义层</strong>的变更 —— 与语义模型无关的表/列变动不进这里。
          <strong>绑定失效</strong>要改字段的物理列绑定；<strong>口径 SQL</strong>
          要改指标实现里的 SQL；<strong>仅表相关</strong>通常只需确认一下。
          <br>
          忽略时<strong>必须写原因</strong> —— 它是给下一个人看的。
        </div>
      </ElAlert>

      <ElTable v-loading="loading" :data="rows" size="small" border>
        <ElTableColumn label="变更" width="150">
          <template #default="{ row }">
            <ElTag :type="(KIND_MAP[row.change_kind]?.type as any) || 'info'" size="small">
              {{ KIND_MAP[row.change_kind]?.text || row.change_kind }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="对象" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="uc-mono">{{ row.db_name }}.{{ row.table_name }}</span>
            <span v-if="row.column_name" class="uc-mono">.{{ row.column_name }}</span>
            <div v-if="row.before_value || row.after_value" class="uc-hint">
              {{ row.before_value || "—" }} → {{ row.after_value || "—" }}
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn label="影响" width="110" align="center">
          <template #default="{ row }">
            <ElTag :type="(LEVEL_MAP[row.impact_level]?.type as any) || 'info'" size="small">
              {{ LEVEL_MAP[row.impact_level]?.text || row.impact_level }}
            </ElTag>
          </template>
        </ElTableColumn>
        <!-- 受影响清单是这条待办的核心内容: 没有它人还得自己去翻哪些口径引用了这列 -->
        <ElTableColumn label="受影响" min-width="260">
          <template #default="{ row }">
            <div v-if="row.impact_detail?.fields?.length" class="uc-hint">
              字段:
              <span v-for="f in row.impact_detail.fields" :key="f.id" class="uc-mono mr-1">
                {{ f.entity }}.{{ f.field }}
              </span>
            </div>
            <div v-if="row.impact_detail?.impls?.length" class="uc-hint">
              口径:
              <span v-for="i in row.impact_detail.impls" :key="i.id" class="uc-mono mr-1">
                {{ i.indicator }}({{ i.role === "primary" ? "主" : "副" }})
              </span>
            </div>
            <div v-if="row.impact_detail?.entities?.length" class="uc-hint">
              实体:
              <span v-for="e in row.impact_detail.entities" :key="e.id" class="uc-mono mr-1">
                {{ e.entity }}
              </span>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="detected_at" label="检出时间" width="160" />
        <ElTableColumn label="操作" width="150" align="center" fixed="right">
          <template #default="{ row }">
            <!-- ★ 只有 pending 行才给操作按钮: 后端对非 pending 的重关一律拒绝,
                 CustomException 走后端错误拦截器会弹 ElMessage.error(data.msg),
                 所以用户会看到的是原后端提示文本而非"服务器错误"。前端必须自己校验的理由是:
                 1. 白跑一次请求(用户已在本地能知道原因没填)
                 2. 后端返回 code=-1 会污染 APM 告警(那不是真故障)
                 3. 弹窗会先关掉再弹 toast, 用户得重新打开并重填 —— 前端校验能让弹窗保持打开原地提示 -->
            <template v-if="canManage && row.status === 'pending'">
              <ElButton size="small" link type="primary" @click="doResolve(row)">
                已处理
              </ElButton>
              <ElButton size="small" link @click="doIgnore(row)">忽略</ElButton>
            </template>
          </template>
        </ElTableColumn>
        <!-- 处理说明: 给只读用户看为什么被关掉。独立列不受 canManage 控制,
             这样每个用户都能看到"原因是给下一个人看的"这个约定 -->
        <ElTableColumn prop="resolve_note" label="处理说明" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.resolve_note || (row.status === 'pending' ? "—" : "") }}
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty
            :image-size="60"
            :description="statusFilter === 'pending' ? '没有待处理的上游变更' : '无记录'"
          />
        </template>
      </ElTable>
    </ElCard>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { onActivated } from "vue";

import UpstreamChangeAPI, {
  type ChangeStatus,
  type ImpactLevel,
  type UpstreamChange,
} from "@/api/module_semantic/upstreamchange";
import { useAuth } from "@/hooks/core/useAuth";

defineOptions({ name: "SemanticUpstreamChange", inheritAttrs: false });

const { hasAuth } = useAuth();
const canManage = computed(() => hasAuth("module_semantic:catalog:manage"));

const loading = ref(false);
const rows = ref<UpstreamChange[]>([]);
const statusFilter = ref<ChangeStatus>("pending");
const levelFilter = ref<ImpactLevel | "">("");

const KIND_MAP: Record<string, { type: string; text: string }> = {
  column_removed: { type: "danger", text: "列已删除" },
  column_type_changed: { type: "warning", text: "列类型变更" },
  column_added: { type: "success", text: "新增列" },
  table_removed: { type: "danger", text: "表已删除" },
};
const LEVEL_MAP: Record<string, { type: string; text: string }> = {
  bound: { type: "danger", text: "绑定失效" },
  sql_ref: { type: "warning", text: "口径 SQL" },
  table_only: { type: "info", text: "仅表相关" },
};

async function load() {
  loading.value = true;
  try {
    const res = await UpstreamChangeAPI.listChange({
      status: statusFilter.value,
      // 空串转 undefined: 别让 impact_level= 这种空参数进 URL 与操作日志
      impact_level: levelFilter.value || undefined,
    });
    rows.value = res.data.data ?? [];
  } catch {
    rows.value = []; // 读失败不该让整页崩
  } finally {
    loading.value = false;
  }
}

async function doResolve(row: UpstreamChange) {
  try {
    await ElMessageBox.confirm(
      `确认已处理「${row.table_name}${row.column_name ? "." + row.column_name : ""}」的变更？`,
      "标记已处理",
      { type: "warning", confirmButtonText: "已处理", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await UpstreamChangeAPI.resolveChange(row.id);
    ElMessage.success("已标记处理");
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || "操作失败");
  }
}

/**
 * 忽略一条变更。
 *
 * ★ 用 prompt 而非 confirm，且在**前端**校验非空：后端确实也会拒绝空原因(CustomException code=-1),
 * 但错误拦截器会弹 ElMessage.error(data.msg)，用户看到的是原后端提示而非"服务器错误"。
 * 前端校验必须前置的理由是: (1)白跑请求, (2)污染 APM 告警, (3)弹窗得关了重填 —— 前端校验让弹窗保持打开原地提示。
 */
async function doIgnore(row: UpstreamChange) {
  let note: string;
  try {
    const { value } = await ElMessageBox.prompt(
      "忽略这条变更的原因？它是给下一个人看的 —— "
      + "一个能无理由关掉的待办箱，三个月后全是「已忽略」而没人记得为什么。",
      "忽略变更",
      {
        inputPlaceholder: "如：上游确认是笔误加的列，已让他们删掉",
        inputValidator: (v: string) => (v && v.trim() ? true : "请写明原因"),
        confirmButtonText: "忽略",
        cancelButtonText: "取消",
        type: "warning",
      },
    );
    note = value;
  } catch {
    return;
  }
  try {
    await UpstreamChangeAPI.ignoreChange(row.id, note);
    ElMessage.success("已忽略");
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || "操作失败");
  }
}

// 用 onActivated 支持 keep-alive: 菜单有 keep_alive 缓存，典型动线是"看待办 → 去实体页改完 → 回来"。
// onActivated 在首次挂载时也会触发(Vue 3 标准行为)，所以既不会漏掉首次加载，也不会重复请求。
onActivated(load);
</script>

<style scoped lang="scss">
.uc-page {
  min-width: 0;
  min-height: 0;
}

.uc-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  /* 标签绝不换行: 被压窄时会变成竖排(治理页实测踩到) */
  white-space: nowrap;
}

.uc-mono {
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 0.78rem;
}

.uc-hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-placeholder);
}
</style>
