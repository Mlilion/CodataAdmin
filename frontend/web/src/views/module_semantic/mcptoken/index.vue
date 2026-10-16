<!-- MCP 令牌：语义中心 - 接入方令牌的签发/轮换/吊销(替代改 env + 重启) -->
<template>
  <div class="mcptoken-page fa-full-height">
    <ElCard class="fa-table-card">
      <template #header>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <span class="font-medium">MCP 访问令牌</span>
            <ElTag size="small" type="info" effect="plain">外部 agent 接入凭证</ElTag>
          </div>
          <div class="flex items-center gap-2">
            <ElButton v-if="canManage" type="primary" size="small" @click="openCreate">
              签发令牌
            </ElButton>
            <ElButton size="small" :loading="loading" @click="refreshAll">刷新</ElButton>
          </div>
        </div>
      </template>

      <!--
        接入地址：光发令牌等于给了钥匙不给门牌号。放在最上面且默认展开 ——
        这是接入方拿到令牌后立刻要问的第一个问题。
      -->
      <div v-if="endpoint" class="mcptoken-endpoint mb-3">
        <div class="mcptoken-endpoint-head">
          <span class="font-medium">MCP 接入地址</span>
          <ElTag v-if="endpoint.mounted" size="small" type="success" effect="plain">
            正在服务
          </ElTag>
          <ElTag v-else size="small" type="danger">未挂载</ElTag>
          <ElTag v-if="endpoint.source === 'config'" size="small" type="info" effect="plain">
            由 SEM_MCP_PUBLIC_URL 指定
          </ElTag>
        </div>

        <div class="mcptoken-endpoint-row">
          <code class="mcptoken-url">{{ endpoint.url }}</code>
          <ElButton size="small" @click="copyUrl">复制地址</ElButton>
          <span v-if="urlCopied" class="text-xs text-green-600">已复制</span>
        </div>

        <!-- 未挂载时必须说清楚，否则人会拿这个地址去配然后收 404 并归因到自己 -->
        <ElAlert v-if="!endpoint.mounted" type="error" :closable="false" show-icon class="mt-2">
          <template #title>该地址当前不服务</template>
          <div class="text-xs leading-relaxed">
            <code>/mcp</code> 没有挂载，现在填这个地址会得到 404。常见原因：
            <code>SEM_MCP_ENABLE=false</code>；或非 dev 环境下
            <strong>一个令牌都没有</strong>（安全门禁会拒绝挂载无鉴权的端点）——
            在本页签发一个令牌后<strong>重启服务</strong>即可。
          </div>
        </ElAlert>
        <!-- 推导值在反代后面会是内网地址，必须提醒核对，否则抄出去连不上 -->
        <div v-else-if="showProxyHint" class="mcptoken-hint mt-1">
          地址由你当前的浏览器访问地址推导。若本平台在反向代理/网关后面，
          请核对是否为接入方可达的地址 —— 不对就配
          <code>SEM_MCP_PUBLIC_URL</code>（写站点根地址即可，<code>/mcp</code> 会自动补）。
        </div>

        <div class="mcptoken-hint mt-1">
          传输方式 <strong>{{ endpoint.transport }}</strong>，
          鉴权头 <code>{{ endpoint.auth_header }}</code>。
          <code>{{ endpoint.url }}/</code> 带尾斜杠亦可。
        </div>
      </div>

      <ElAlert type="warning" :closable="false" show-icon class="mb-3">
        <template #title>明文令牌只在签发/轮换时显示一次</template>
        <div class="text-xs leading-relaxed">
          库里存的是不可逆哈希，<strong>之后任何地方都取不回明文</strong>（库泄露也拿不到可用令牌）。
          忘了只能<strong>轮换</strong>重发 —— 与 GitHub / Stripe 的 API Key 同一语义。
          <br>
          令牌持有者能查询语义口径并执行参数化数仓只读查询；要按人收窄可见范围，
          请<strong>绑定用户</strong>并把模型设为「限制可见」。
        </div>
      </ElAlert>

      <ElTable v-loading="loading" :data="tokens" size="small" border>
        <ElTableColumn prop="name" label="调用方" min-width="120" show-overflow-tooltip />
        <ElTableColumn label="令牌" width="150">
          <template #default="{ row }">
            <span class="mcptoken-mask">{{ row.token_prefix }}••••••</span>
          </template>
        </ElTableColumn>
        <ElTableColumn label="绑定用户" width="130">
          <template #default="{ row }">
            <span v-if="row.bound_username">{{ row.bound_username }}</span>
            <ElTooltip v-else content="未绑定用户的令牌只能访问「所有调用方可见」的模型" placement="top">
              <ElTag size="small" type="info">未绑定</ElTag>
            </ElTooltip>
          </template>
        </ElTableColumn>
        <ElTableColumn label="状态" width="94" align="center">
          <template #default="{ row }">
            <ElTag v-if="row.revoked" size="small" type="danger">已吊销</ElTag>
            <ElTag v-else-if="row.expired" size="small" type="warning">已过期</ElTag>
            <ElTag v-else size="small" type="success">可用</ElTag>
          </template>
        </ElTableColumn>
        <!-- 最后使用时间是"敢不敢删这个 token"的唯一依据，所以要显眼 -->
        <ElTableColumn label="最后使用" width="168">
          <template #default="{ row }">
            <span v-if="row.last_used_at">{{ row.last_used_at }}</span>
            <ElTag v-else size="small" type="info" effect="plain">从未使用</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="过期" width="120">
          <template #default="{ row }">
            {{ row.expires_at ? String(row.expires_at).slice(0, 10) : "长期" }}
          </template>
        </ElTableColumn>
        <ElTableColumn prop="description" label="备注" min-width="120" show-overflow-tooltip />
        <ElTableColumn v-if="canManage" label="操作" width="200" align="center" fixed="right">
          <template #default="{ row }">
            <ElButton size="small" link type="primary" @click="openEdit(row)">编辑</ElButton>
            <ElButton
              size="small" link type="warning" :disabled="row.revoked"
              @click="doRotate(row)"
            >
              轮换
            </ElButton>
            <ElButton
              size="small" link type="danger" :disabled="row.revoked"
              @click="doRevoke(row)"
            >
              吊销
            </ElButton>
            <ElButton size="small" link @click="doDelete(row)">删除</ElButton>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty :image-size="60" description="尚未签发任何令牌" />
        </template>
      </ElTable>

      <!-- env 里的令牌：不列出来的话，"当前有哪些 token 能进来"在页面上就没有完整答案 -->
      <template v-if="envTokens.length">
        <div class="mcptoken-section">
          配置文件中的令牌（<code>SEM_MCP_TOKENS</code> / <code>SEM_MCP_TOKEN</code>）
        </div>
        <ElAlert type="info" :closable="false" class="mb-2">
          <div class="text-xs">
            这些令牌<strong>仍然有效</strong>但只读 —— 改它们要动配置文件并重启服务，
            页面上无法轮换或吊销。建议逐步迁到上面的库签发方式。
          </div>
        </ElAlert>
        <ElTable :data="envTokens" size="small" border>
          <ElTableColumn prop="name" label="调用方" min-width="140" />
          <ElTableColumn label="绑定用户" width="140">
            <template #default="{ row }">
              {{ row.bound_username || "未绑定" }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="来源" width="140">
            <template #default>
              <ElTag size="small" type="info">配置文件(只读)</ElTag>
            </template>
          </ElTableColumn>
        </ElTable>
      </template>
    </ElCard>

    <!-- 签发/编辑 -->
    <FaDialog
      v-model="formVisible"
      :title="form.id ? '编辑令牌' : '签发令牌'"
      width="520px"
      :form-mode="form.id ? 'update' : 'create'"
      :confirm-loading="saving"
      @cancel="formVisible = false"
      @confirm="doSave"
    >
      <ElForm label-width="100px">
        <ElFormItem label="调用方名" required>
          <ElInput
            v-model="form.name" :disabled="!!form.id" maxlength="50"
            placeholder="如 codata / ops-bot"
          />
          <div class="mcptoken-hint">
            会记进 query 日志的 <code>caller</code> 字段。
            {{ form.id ? "签发后不可改(改了会让历史统计错位)" : "不同接入方用不同名字，治理时才答得出「是谁在这么问」" }}
          </div>
        </ElFormItem>
        <ElFormItem label="绑定用户">
          <ElSelect
            v-model="form.bound_username"
            filterable remote reserve-keyword clearable
            :remote-method="searchUsers" :loading="userLoading"
            placeholder="搜索用户名(留空则只能看 public 模型)"
            class="w-full"
          >
            <ElOption
              v-for="u in userOptions" :key="u.id"
              :label="`${u.name || u.username}（${u.username}）`" :value="u.username!"
            />
          </ElSelect>
          <div class="mcptoken-hint">
            绑上才有模型级隔离：该令牌能看到这个用户有权访问的「限制可见」模型。
            留空不是超级权限，而是<strong>只能看 public</strong>。
          </div>
        </ElFormItem>
        <ElFormItem label="过期时间">
          <ElDatePicker
            v-model="form.expires_at" type="datetime" value-format="YYYY-MM-DD HH:mm:ss"
            placeholder="留空为长期有效" class="w-full"
          />
          <div class="mcptoken-hint">临时给外部联调时建议设一个，到期自动失效</div>
        </ElFormItem>
        <ElFormItem label="备注">
          <ElInput
            v-model="form.description" type="textarea" :rows="2" maxlength="200"
            placeholder="用途/负责人，便于日后判断能不能吊销"
          />
        </ElFormItem>
      </ElForm>
    </FaDialog>

    <!-- 明文一次性展示：关掉就再也看不到，故用独立弹窗 + 复制按钮 -->
    <FaDialog
      v-model="secretVisible"
      title="令牌已签发"
      width="580px"
      form-mode="detail"
      confirm-text="我已保存"
      @confirm="secretVisible = false"
    >
      <ElAlert type="error" :closable="false" show-icon class="mb-3">
        <template #title>请立即复制保存 —— 关闭后无法再次查看</template>
        <div class="text-xs">
          库里只存不可逆哈希。丢了只能「轮换」重发一把（旧的会同时失效）。
        </div>
      </ElAlert>
      <div class="mcptoken-secret">{{ secretToken }}</div>
      <div class="mt-3 flex items-center gap-2">
        <ElButton type="primary" size="small" @click="copySecret">复制令牌</ElButton>
        <span v-if="copied" class="text-xs text-green-600">已复制</span>
      </div>
      <!--
        地址与令牌必须在同一屏给全:这是唯一能看到明文的时刻,
        只给 header 会逼人关掉弹窗去别处找 URL —— 而关掉就再也看不到明文了。
      -->
      <div class="mcptoken-section">在 MCP client 中这样配</div>
      <pre class="mcptoken-code">{{ clientSnippet }}</pre>
      <div class="mt-2 flex items-center gap-2">
        <ElButton size="small" @click="copySnippet">复制这段配置</ElButton>
        <span v-if="snippetCopied" class="text-xs text-green-600">已复制</span>
      </div>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";

import McpTokenAPI, {
  type EnvToken,
  type McpEndpoint,
  type McpToken,
  type TokenForm,
} from "@/api/module_semantic/mcptoken";
import UserAPI, { type UserInfo } from "@/api/module_system/user";
import { useAuth } from "@/hooks/core/useAuth";

import { buildClientSnippet, needsProxyHint } from "./endpoint";

defineOptions({ name: "SemanticMcpToken", inheritAttrs: false });

const { hasAuth } = useAuth();
/** 发令牌等于发访问权，故是独立权限点，不沿用任何读权限 */
const canManage = computed(() => hasAuth("module_semantic:mcp:token"));

const loading = ref(false);
const saving = ref(false);
const tokens = ref<McpToken[]>([]);
const envTokens = ref<EnvToken[]>([]);

const formVisible = ref(false);
const form = ref<TokenForm & { id?: number }>({ name: "" });

const secretVisible = ref(false);
const secretToken = ref("");
const { copy, copied } = useClipboard({ legacy: true });

const endpoint = ref<McpEndpoint | null>(null);
/**
 * 三个复制按钮各自独立的"已复制"状态。
 *
 * ★ 不共用一个 useClipboard：共用会让复制令牌时"已复制"同时出现在地址旁边,
 * 看起来像复制错了东西 —— 而这一屏关掉就再也看不到明文, 不能让人怀疑自己复制了什么。
 */
const { copy: copyUrlRaw, copied: urlCopied } = useClipboard({ legacy: true });
const { copy: copySnippetRaw, copied: snippetCopied } = useClipboard({ legacy: true });

/** 一次给全地址与令牌 —— 关掉弹窗后明文就取不回了 */
const clientSnippet = computed(() => buildClientSnippet(endpoint.value, secretToken.value));
/** 推导来源才提醒核对；显式配过的人已经做过这个决定，再提醒是噪声 */
const showProxyHint = computed(() => needsProxyHint(endpoint.value));

const userOptions = ref<UserInfo[]>([]);
const userLoading = ref(false);

async function loadTokens() {
  loading.value = true;
  try {
    const res = await McpTokenAPI.listToken();
    tokens.value = res.data.data ?? [];
  } catch {
    tokens.value = [];
  } finally {
    loading.value = false;
  }
}

async function loadEnvTokens() {
  try {
    const res = await McpTokenAPI.listEnvToken();
    envTokens.value = res.data.data ?? [];
  } catch {
    envTokens.value = []; // env 读不到不该让整页崩
  }
}

async function loadEndpoint() {
  try {
    const res = await McpTokenAPI.getEndpoint();
    endpoint.value = res.data.data ?? null;
  } catch {
    endpoint.value = null; // 取不到地址不该让整页崩(令牌管理本身仍要能用)
  }
}

async function refreshAll() {
  await Promise.all([loadTokens(), loadEnvTokens(), loadEndpoint()]);
}

async function searchUsers(keyword: string) {
  userLoading.value = true;
  try {
    const res = await UserAPI.listUser({ page_no: 1, page_size: 20, name: keyword || undefined });
    userOptions.value = res.data.data?.items || [];
  } catch {
    userOptions.value = [];
  } finally {
    userLoading.value = false;
  }
}

function openCreate() {
  form.value = { name: "" };
  void searchUsers("");
  formVisible.value = true;
}

function openEdit(row: McpToken) {
  form.value = {
    id: row.id,
    name: row.name,
    bound_username: row.bound_username ?? undefined,
    expires_at: row.expires_at ?? undefined,
    description: row.description ?? undefined,
  };
  void searchUsers("");
  formVisible.value = true;
}

/** 展示明文（唯一一次机会），顺手放进剪贴板候选 */
function showSecret(token: string) {
  secretToken.value = token;
  secretVisible.value = true;
}

async function copySecret() {
  await copy(secretToken.value);
}

async function copyUrl() {
  if (endpoint.value) await copyUrlRaw(endpoint.value.url);
}

async function copySnippet() {
  await copySnippetRaw(clientSnippet.value);
}

async function doSave() {
  const f = form.value;
  if (!f.name?.trim()) {
    ElMessage.warning("调用方名必填");
    return;
  }
  saving.value = true;
  try {
    if (f.id) {
      await McpTokenAPI.updateToken(f.id, {
        bound_username: f.bound_username ?? null,
        expires_at: f.expires_at ?? null,
        description: f.description ?? null,
      });
      ElMessage.success("已更新");
      formVisible.value = false;
    } else {
      const res = await McpTokenAPI.createToken(f);
      formVisible.value = false;
      showSecret(res.data.data.token);
    }
    await refreshAll();
  } catch (e: any) {
    ElMessage.error(e?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

async function doRotate(row: McpToken) {
  try {
    await ElMessageBox.confirm(
      `轮换「${row.name}」的令牌？\n`
      + `旧令牌会**立刻失效** —— 该接入方在换上新令牌之前会一直 401。\n`
      + `调用方名与绑定用户保持不变。`,
      "确认轮换",
      { type: "warning", confirmButtonText: "轮换", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    const res = await McpTokenAPI.rotateToken(row.id);
    showSecret(res.data.data.token);
    await refreshAll();
  } catch (e: any) {
    ElMessage.error(e?.message || "轮换失败");
  }
}

async function doRevoke(row: McpToken) {
  const neverUsed = !row.last_used_at;
  try {
    await ElMessageBox.confirm(
      `吊销「${row.name}」？该令牌立刻不可用，记录保留以留审计痕迹。\n`
      + (neverUsed
        ? "该令牌从未被使用过，吊销风险低。"
        : `⚠️ 最后使用于 ${row.last_used_at} —— 可能仍有接入方在用，吊销后会立刻 401。`),
      "确认吊销",
      { type: "warning", confirmButtonText: "吊销", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await McpTokenAPI.revokeToken(row.id);
    ElMessage.success("已吊销");
    await refreshAll();
  } catch (e: any) {
    ElMessage.error(e?.message || "吊销失败");
  }
}

async function doDelete(row: McpToken) {
  try {
    await ElMessageBox.confirm(
      `删除「${row.name}」的记录？\n`
      + `出事后要查"谁在什么时候发过什么"，日常建议用「吊销」而非删除。`,
      "确认删除",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await McpTokenAPI.deleteToken([row.id]);
    ElMessage.success("已删除");
    await refreshAll();
  } catch (e: any) {
    ElMessage.error(e?.message || "删除失败");
  }
}

onMounted(refreshAll);
</script>

<style scoped lang="scss">
.mcptoken-page {
  min-width: 0;
  min-height: 0;
}

.mcptoken-mask {
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 0.78rem;
  color: var(--el-text-color-regular);
}

.mcptoken-hint {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-placeholder);
}

.mcptoken-section {
  margin: 16px 0 8px;
  font-size: 13px;
  font-weight: 500;
}

.mcptoken-secret {
  padding: 12px;
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 0.82rem;
  word-break: break-all;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}

.mcptoken-code {
  padding: 10px 12px;
  font-size: 0.75rem;
  word-break: break-all;
  white-space: pre-wrap;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.mcptoken-endpoint {
  padding: 12px 14px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
}

.mcptoken-endpoint-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 13px;
}

.mcptoken-endpoint-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.mcptoken-url {
  padding: 5px 10px;
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 0.82rem;
  word-break: break-all;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
}
</style>
