<script setup lang="ts">
import type { AiConfigForm } from "@/api/module_system/aiconfig";
import type { RetrievalStatus, SemanticReadiness } from "@/api/module_semantic/retrieval";

import { ElMessageBox } from "element-plus";

import AiConfigAPI from "@/api/module_system/aiconfig";
import RetrievalAPI from "@/api/module_semantic/retrieval";
import { useAuth } from "@/hooks/core/useAuth";

defineOptions({ name: "SystemAiConfig" });

const { hasAuth } = useAuth();
const canEdit = computed(() => hasAuth("module_system:aiconfig:update"));
/** 重建索引是检索侧权限，与嵌入配置的编辑权分开 —— 有人只该重建、不该改模型配置 */
const canRebuild = computed(() => hasAuth("module_semantic:retrieval:rebuild"));

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const embedding = ref(false);
const rebuilding = ref(false);
const rebuildingVectors = ref(false);

const form = ref<AiConfigForm>({
  provider: "openai_like",
  base_url: "",
  api_key: "",
  model: "",
  dimensions: 1024,
  enabled: false,
  description: "",
});
/** 已存 key 的掩码；用户不重填则保持不变 */
const keyMasked = ref("");
const keyConfigured = ref(false);
/** 载入时的维度：用于提示"改了维度会重建向量索引" */
const loadedDimensions = ref(0);
const status = ref<RetrievalStatus | null>(null);
const readiness = ref<SemanticReadiness | null>(null);
const testResult = ref<{ ok: boolean; text: string } | null>(null);

const dimensionWillRebuild = computed(
  () => loadedDimensions.value > 0 && form.value.dimensions !== loadedDimensions.value,
);

const PROVIDERS = [
  { label: "OpenAI 兼容", value: "openai_like" },
  { label: "TEI (Text Embeddings Inference)", value: "tei" },
];

/** 常见模型的维度，选了自动填 —— 维度填错是最常见的配置问题 */
const KNOWN_MODELS = [
  { label: "text-embedding-3-small", dimensions: 1536 },
  { label: "text-embedding-3-large", dimensions: 3072 },
  { label: "text-embedding-ada-002", dimensions: 1536 },
  { label: "bge-large-zh-v1.5", dimensions: 1024 },
  { label: "bge-m3", dimensions: 1024 },
  { label: "embedding-3 (智谱)", dimensions: 2048 },
];

async function loadConfig() {
  loading.value = true;
  try {
    const res = await AiConfigAPI.detail();
    const data = res.data.data;
    form.value = {
      provider: data.provider,
      base_url: data.base_url,
      api_key: "", // 不回填明文；留空即不修改
      model: data.model,
      dimensions: data.dimensions,
      enabled: data.enabled,
      description: data.description ?? "",
    };
    keyMasked.value = data.api_key_masked;
    keyConfigured.value = data.api_key_configured;
    loadedDimensions.value = data.dimensions;
  } finally {
    loading.value = false;
  }
}

async function loadStatus() {
  try {
    const res = await RetrievalAPI.status();
    status.value = res.data.data;
  } catch {
    status.value = null; // 检索副库不可达不该让整页报错
  }
}

async function loadReadiness() {
  try {
    const res = await RetrievalAPI.readiness();
    readiness.value = res.data.data;
  } catch {
    readiness.value = null; // 同上: 就绪自检读不到绝不能阻塞配置表单
  }
}

async function handleSave() {
  if (form.value.enabled && (!form.value.base_url.trim() || !form.value.model.trim())) {
    ElMessage.warning("启用向量检索前需填写 Base URL 与模型名");
    return;
  }
  if (dimensionWillRebuild.value) {
    try {
      await ElMessageBox.confirm(
        `维度将由 ${loadedDimensions.value} 改为 ${form.value.dimensions}。`
        + `\n现有向量会全部清空并自动重新计算，期间向量检索精度下降（前三路不受影响）。`,
        "确认变更维度",
        { type: "warning", confirmButtonText: "确认变更", cancelButtonText: "取消" },
      );
    } catch {
      return;
    }
  }

  saving.value = true;
  try {
    const res = await AiConfigAPI.update(form.value);
    const result = res.data.data;
    if (result.dimension_changed) {
      ElMessage.success(
        `已保存。维度 ${result.old_dimensions} → ${result.new_dimensions}，`
        + `清空 ${result.cleared} 条旧向量，将自动重算`,
      );
    } else {
      ElMessage.success("保存成功");
    }
    testResult.value = null;
    await Promise.all([loadConfig(), loadStatus(), loadReadiness()]);
  } finally {
    saving.value = false;
  }
}

async function handleTest() {
  if (!form.value.base_url.trim() || !form.value.model.trim()) {
    ElMessage.warning("请先填写 Base URL 与模型名");
    return;
  }
  testing.value = true;
  testResult.value = null;
  try {
    // 测**表单里的当前值**，不必先保存（保存一份连不通的配置本身就不该发生）
    const res = await AiConfigAPI.test(form.value);
    const data = res.data.data;
    testResult.value = data.ok
      ? { ok: true, text: `连接正常，端点返回 ${data.dimensions} 维（模型 ${data.model}）` }
      : { ok: false, text: data.error || "连接失败" };
  } catch (e: any) {
    testResult.value = { ok: false, text: e?.message || "连接失败" };
  } finally {
    testing.value = false;
  }
}

async function handleEmbed() {
  embedding.value = true;
  try {
    const res = await RetrievalAPI.embed();
    const data = res.data.data;
    if (data.skipped) {
      ElMessage.warning("嵌入未启用，无需补齐");
    } else if (data.failed > 0) {
      ElMessage.warning(`补齐 ${data.updated} 条，失败 ${data.failed} 条：${data.error || ""}`);
    } else {
      ElMessage.success(`已补齐 ${data.updated} 条向量`);
    }
    await loadStatus();
  } finally {
    embedding.value = false;
  }
}

/**
 * 重建全部已发布模型的检索索引。
 *
 * readiness 的 blocker「模型已发布 vN，索引仍是 vM，请重建检索索引」指的就是这个动作 ——
 * 此前它只有后端端点与权限点、没有任何调用方，等于让运维照着提示做一件 UI 上做不到的事。
 */
async function handleRebuild() {
  try {
    await ElMessageBox.confirm(
      "将按各模型最新发布快照重建全部检索索引（幂等，可重复执行）。\n"
      + "期间已在库的记录仍可被检索到，重建完成后索引版本与发布版本对齐。",
      "确认重建检索索引",
      { type: "warning", confirmButtonText: "开始重建", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }

  rebuilding.value = true;
  try {
    const res = await RetrievalAPI.rebuild();
    const data = res.data.data;
    // skipped 时各计数均为 0，若照常报"重建完成"运维会以为索引已新 —— 必须分开说
    if (data.skipped) {
      ElMessage.warning("检索副库总开关未启用（SEMANTIC_RETRIEVAL_ENABLE=false），未执行重建");
    } else {
      ElMessage.success(
        `重建完成：${data.models} 个模型，新增 ${data.inserted} / 更新 ${data.updated} / 清理 ${data.pruned} 条`,
      );
    }
    // 重建后索引版本变了，就绪度与状态都得重读 —— 否则 blocker 还挂在页面上
    await Promise.all([loadStatus(), loadReadiness()]);
  } finally {
    rebuilding.value = false;
  }
}

/**
 * 按当前配置重建 pgvector 列宽（清空全部旧向量）。
 *
 * ★ 与「保存」的区别：保存是"改配置顺带对齐列宽"，本按钮是"配置没变、只要把列重建一次"。
 * 后者的真实场景是副库被重建过 / 列宽被手工改过 —— 此时表单值与库里配置一致，
 * 保存会因"维度没变"而跳过重建（有测试固化这个行为），于是无路可走。
 */
async function handleRebuildVectors() {
  try {
    await ElMessageBox.confirm(
      `将 pgvector 列宽重建为 ${form.value.dimensions} 维。\n`
      + `现有向量会**全部清空**并重新计算（期间向量检索精度下降，前三路不受影响）。`,
      "确认重建向量列",
      { type: "warning", confirmButtonText: "重建", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  rebuildingVectors.value = true;
  try {
    const res = await AiConfigAPI.rebuildVectors();
    const data = res.data.data;
    // skipped 时什么都没做，报"重建完成"会让人以为列宽已对齐
    if (data.skipped) {
      ElMessage.warning("检索副库未启用（SEMANTIC_RETRIEVAL_ENABLE=false），未执行重建");
    } else {
      ElMessage.success(`已重建列宽，清空 ${data.cleared} 条旧向量，将自动重算`);
    }
    await Promise.all([loadStatus(), loadReadiness()]);
  } catch (e: any) {
    ElMessage.error(e?.message || "重建失败");
  } finally {
    rebuildingVectors.value = false;
  }
}

function onModelPick(label: string) {
  const known = KNOWN_MODELS.find((m) => m.label === label);
  if (known) form.value.dimensions = known.dimensions;
}

onMounted(() => {
  loadConfig();
  loadStatus();
  loadReadiness();
});
</script>

<template>
  <FaPageMain>
    <ElAlert type="info" :closable="false" class="mb-4">
      <template #title>嵌入模型用于语义检索的<strong>向量召回</strong>（第四级）</template>
      <div class="text-xs leading-relaxed">
        精确匹配、模糊匹配、全文检索三路不依赖它，未配置时检索仍可用。
        向量路补的是<strong>字面无重叠</strong>时的语义近似（问法与资产名用词完全不同、
        前三路零命中的场景）。它的分数区间低于前三路，<strong>不会改变已召回结果的排序</strong>。
        本配置为<strong>平台级</strong> —— 检索索引全局共用，故只有一份。
      </div>
    </ElAlert>

    <ElRow :gutter="16">
      <ElCol :lg="15" :md="24">
        <ElCard shadow="never">
          <template #header>
            <div class="flex items-center justify-between">
              <span class="font-medium">嵌入模型配置</span>
              <ElTag v-if="form.enabled" type="success" size="small">向量路已启用</ElTag>
              <ElTag v-else type="info" size="small">向量路未启用</ElTag>
            </div>
          </template>

          <ElForm v-loading="loading" label-width="120px" :disabled="!canEdit">
            <ElFormItem label="启用向量检索">
              <ElSwitch v-model="form.enabled" />
              <span class="ml-3 text-xs text-gray-400">
                关闭后检索退化为前三路（精确/模糊/全文），不影响可用性
              </span>
            </ElFormItem>

            <ElFormItem label="提供商">
              <ElSelect v-model="form.provider" class="w-full">
                <ElOption
                  v-for="p in PROVIDERS" :key="p.value" :label="p.label" :value="p.value"
                />
              </ElSelect>
            </ElFormItem>

            <ElFormItem label="Base URL" :required="form.enabled">
              <ElInput
                v-model="form.base_url"
                placeholder="如 https://api.openai.com/v1（不含 /embeddings）"
              />
            </ElFormItem>

            <ElFormItem label="API Key">
              <ElInput
                v-model="form.api_key" type="password" show-password
                :placeholder="keyConfigured ? `已配置（${keyMasked}），留空则不修改` : '请输入 API Key'"
              />
            </ElFormItem>

            <ElFormItem label="模型名" :required="form.enabled">
              <ElSelect
                v-model="form.model" class="w-full" filterable allow-create
                default-first-option placeholder="选择或直接输入模型名"
                @change="onModelPick"
              >
                <ElOption
                  v-for="m in KNOWN_MODELS" :key="m.label"
                  :label="`${m.label}（${m.dimensions} 维）`" :value="m.label"
                />
              </ElSelect>
              <span class="mt-1 text-xs text-gray-400">
                选内置模型会自动填维度；自定义模型请手填正确维度
              </span>
            </ElFormItem>

            <ElFormItem label="向量维度">
              <ElInputNumber v-model="form.dimensions" :min="1" :max="8192" :step="128" />
              <ElTag v-if="dimensionWillRebuild" type="warning" size="small" class="ml-3">
                将重建向量索引
              </ElTag>
              <div v-if="dimensionWillRebuild" class="mt-1 text-xs text-orange-500">
                维度改变后现有 {{ status?.node_count ?? 0 }} 条记录的向量会全部清空并重新计算
              </div>
            </ElFormItem>

            <ElFormItem label="备注">
              <ElInput v-model="form.description" type="textarea" :rows="2" />
            </ElFormItem>

            <ElFormItem v-if="canEdit">
              <ElButton type="primary" :loading="saving" @click="handleSave">
                保存
              </ElButton>
              <ElButton :loading="testing" @click="handleTest">
                测试连接
              </ElButton>
              <span class="ml-2 text-xs text-gray-400">测试当前表单值，无需先保存</span>
            </ElFormItem>
          </ElForm>

          <ElAlert
            v-if="testResult" :type="testResult.ok ? 'success' : 'error'"
            :closable="false" show-icon class="mt-2"
          >
            {{ testResult.text }}
          </ElAlert>
        </ElCard>
      </ElCol>

      <ElCol :lg="9" :md="24">
        <ElCard shadow="never">
          <template #header>
            <span class="font-medium">检索索引状态</span>
          </template>

          <!-- 就绪自检：blockers 是给运维的行动清单。读失败时整块不显示，
               绝不阻塞下面的配置表单（与 loadStatus 同一降级原则） -->
          <ElAlert
            v-if="readiness && !readiness.ready"
            type="warning"
            :closable="false"
            show-icon
            class="mb-3"
          >
            <template #title>语义平台尚未就绪（{{ readiness.blockers.length }} 项待处理）</template>
            <ul class="readiness-blockers">
              <li v-for="(b, i) in readiness.blockers" :key="i">{{ b }}</li>
            </ul>
            <!-- 索引过期是 blockers 里唯一能在本页一键修掉的一项，就把动作放在提示旁边：
                 blocker 说"请重建检索索引"，按钮就得在同一处，否则运维还得自己找 -->
            <ElButton
              v-if="readiness.index_freshness?.stale && canRebuild"
              size="small" type="warning" plain class="mt-2" :loading="rebuilding"
              @click="handleRebuild"
            >
              重建检索索引
            </ElButton>
          </ElAlert>
          <ElAlert
            v-else-if="readiness?.ready"
            type="success"
            :closable="false"
            show-icon
            class="mb-3"
            title="语义平台已就绪（检索、模型发布、数仓连接均正常）"
          />

          <template v-if="status">
            <ElDescriptions :column="1" border size="small">
              <ElDescriptionsItem label="检索副库">
                <ElTag v-if="status.reachable && status.schema_ready" type="success" size="small">
                  已连接
                </ElTag>
                <!-- 连上但没建表要单独提示：跑 init-retrieval 就好，不是网络问题 -->
                <ElTag v-else-if="status.reachable" type="warning" size="small">未建表</ElTag>
                <ElTag v-else-if="status.enabled" type="danger" size="small">不可达</ElTag>
                <ElTag v-else type="info" size="small">未启用</ElTag>
              </ElDescriptionsItem>
              <ElDescriptionsItem label="检索记录">
                {{ status.node_count }} 条
              </ElDescriptionsItem>
              <ElDescriptionsItem label="别名">
                {{ status.alias_count }} 条
              </ElDescriptionsItem>
              <ElDescriptionsItem label="待生成向量">
                <span :class="status.pending_embedding > 0 ? 'text-orange-500' : ''">
                  {{ status.pending_embedding }} 条
                </span>
              </ElDescriptionsItem>
              <ElDescriptionsItem v-if="status.vector_dim" label="向量列宽">
                {{ status.vector_dim }} 维
                <ElTag v-if="status.dim_mismatch" type="danger" size="small" class="ml-2">
                  与配置不符
                </ElTag>
              </ElDescriptionsItem>
              <ElDescriptionsItem label="召回路">
                <ElTag size="small" class="mr-1">精确</ElTag>
                <ElTag size="small" class="mr-1">模糊</ElTag>
                <ElTag v-if="status.fts_enabled" size="small" type="success" class="mr-1">
                  全文
                </ElTag>
                <ElTag v-if="status.vector_effective" size="small" type="success">
                  向量
                </ElTag>
                <ElTag v-else size="small" type="info">向量(关)</ElTag>
              </ElDescriptionsItem>
              <!-- 向量路有两个开关(env 熔断 / AI 配置启用)，未生效时必须说清是哪个 ——
                   只显示一个"关"字运维无从判断该改哪里 -->
              <ElDescriptionsItem v-if="status.vector_inactive_reason" label="向量未生效">
                <span class="text-orange-500">{{ status.vector_inactive_reason }}</span>
              </ElDescriptionsItem>
            </ElDescriptions>

            <!-- 列宽不符时补齐会全部失败，必须给出可执行的修复动作 -->
            <ElAlert
              v-if="status.dim_mismatch" type="error" :closable="false" show-icon class="mt-3"
            >
              <template #title>向量列宽与配置不一致</template>
              <div class="text-xs">
                库中列宽 {{ status.vector_dim }} 维，配置为 {{ form.dimensions }} 维。
                此状态下补齐向量会全部失败。点「保存」会自动对齐列宽；
                若不想改动配置，用下面的「重建向量列」直接按当前配置重建。
              </div>
              <ElButton
                v-if="canEdit" size="small" type="danger" plain class="mt-2"
                :loading="rebuildingVectors" @click="handleRebuildVectors"
              >
                重建向量列
              </ElButton>
            </ElAlert>

            <div v-if="status.pending_embedding > 0" class="mt-3">
              <ElProgress
                :percentage="status.node_count
                  ? Math.round((1 - status.pending_embedding / status.node_count) * 100)
                  : 0"
                :stroke-width="10"
              />
              <div class="mt-1 text-xs text-gray-400">
                向量补齐进度（{{ status.node_count - status.pending_embedding }}/{{ status.node_count }}）
              </div>
            </div>

            <div class="mt-4 flex gap-2">
              <ElButton
                v-if="canEdit"
                size="small" :loading="embedding"
                :disabled="!form.enabled || status.pending_embedding === 0"
                @click="handleEmbed"
              >
                补齐向量
              </ElButton>
              <!-- 常态入口：索引没过期时也可能要重建（副库重建、prune 后对账） -->
              <ElButton
                v-if="canRebuild"
                size="small" :loading="rebuilding" :disabled="!status.enabled"
                @click="handleRebuild"
              >
                重建索引
              </ElButton>
              <ElButton size="small" @click="loadStatus">
                刷新
              </ElButton>
            </div>
          </template>

          <ElEmpty v-else description="检索副库未启用或不可达" :image-size="60" />
        </ElCard>
      </ElCol>
    </ElRow>
  </FaPageMain>
</template>

<style lang="scss" scoped>
.readiness-blockers {
  padding-left: 18px;
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.7;
  list-style: disc;
}
</style>
