<!-- 同义词治理：语义中心 - 别名挖掘候选 + 零召回清单(逐条人工采纳) -->
<template>
  <div class="querylog-page fa-full-height">
    <ElCard class="fa-table-card">
      <template #header>
        <div class="flex items-center justify-between flex-wrap gap-2">
          <div class="flex items-center gap-2">
            <span class="font-medium">同义词治理</span>
            <ElTag size="small" type="info" effect="plain">来自真实提问</ElTag>
          </div>
          <!-- ★ flex-wrap + shrink-0：这排有 6 个控件，窄屏时挤在一行会把「调用方」
               三个字压成竖排、下拉压成「全..」（浏览器实测踩到）。
               让整组按需换行、各控件自身不被压缩 -->
          <div class="flex flex-wrap items-center gap-2">
            <!-- 调用方筛选：巡检脚本与真人提问的采纳标准不同，不分开看无法判断该不该采纳。
                 用下拉而非输入框 —— 手打打错一个字得到空列表，看起来像"没问过"而非"打错了" -->
            <span class="querylog-label shrink-0">调用方</span>
            <!-- ★ 宽度用 style 而非 w-36：ElSelect 的根节点会吃掉 class 上的宽度类，
                 结果下拉撑满整行、每个控件各占一行（浏览器实测：控件组高 140px）。
                 内联 style 直接落到根节点上，是这里唯一稳的写法 -->
            <ElSelect
              v-model="callerFilter" size="small" style="width: 150px; flex: none" clearable
              placeholder="全部" @change="reloadLists"
            >
              <ElOption value="" label="全部调用方" />
              <ElOption
                v-for="c in callerOptions" :key="c.caller"
                :label="`${c.caller}（${c.hits}）`" :value="c.caller"
              />
            </ElSelect>
            <span class="querylog-label shrink-0">回看</span>
            <ElSelect
              v-model="sinceDays" size="small" style="width: 96px; flex: none"
              @change="refreshAll"
            >
              <ElOption :value="7" label="7 天" />
              <ElOption :value="30" label="30 天" />
              <ElOption :value="90" label="90 天" />
            </ElSelect>
            <ElButton size="small" :loading="loading" @click="refreshAll">刷新</ElButton>
            <!-- 日志是高频写入表(每次检索一条)，定时任务每周清一次；
                 这里是"表已经涨大、现在就要清"的补救入口 -->
            <ElButton
              v-if="canApprove" size="small" :loading="purging" @click="handlePurge"
            >
              清理日志
            </ElButton>
          </div>
        </div>
      </template>

      <ElAlert type="info" :closable="false" show-icon class="mb-3">
        <template #title>
          冷启动别名覆盖不了真实用户措辞。这里的候选来自被反复提问且**取数成功**的说法；
          采纳后并进该资产的别名（不覆盖手工补的），<b>立即生效</b>。
        </template>
      </ElAlert>

      <ElTabs v-model="activeTab">
        <!-- 别名候选 -->
        <ElTabPane name="candidates">
          <template #label>
            <span>别名候选<ElBadge v-if="candidates.length" :value="candidates.length" class="ml-2" /></span>
          </template>

          <div class="flex items-center gap-2 mb-2">
            <span class="querylog-label">最少采纳次数</span>
            <ElInputNumber v-model="minHits" :min="1" :max="100" size="small" @change="loadCandidates" />
            <span class="querylog-hint">
              只统计明确采纳的问法；未反馈的不计入 —— 否则会把检索的错误答案固化成别名
            </span>
          </div>

          <ElTable v-loading="loading" :data="candidates" size="small" border>
            <ElTableColumn prop="alias" label="候选别名" min-width="160">
              <template #default="{ row }">
                <span class="font-medium">{{ row.alias }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="node_type" label="资产类型" width="110">
              <template #default="{ row }">
                <ElTag size="small" effect="plain">{{ row.node_type || "-" }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="node_code" label="归属资产" min-width="180" show-overflow-tooltip />
            <ElTableColumn prop="hits" label="采纳次数" width="100" align="right" />
            <!-- 是谁在这么问：决定该不该采纳。脚本刷出来的措辞采纳成别名没有价值 -->
            <ElTableColumn label="调用方" width="150">
              <template #default="{ row }">
                <ElTag
                  v-for="c in row.callers" :key="c"
                  size="small" effect="plain" class="mr-1"
                >
                  {{ c }}
                </ElTag>
                <span v-if="!row.callers?.length" class="querylog-hint">未记录</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="110" fixed="right">
              <template #default="{ row }">
                <ElButton
                  v-if="canApprove"
                  size="small"
                  type="primary"
                  link
                  :loading="approving === rowKey(row)"
                  @click="handleApprove(row)"
                >
                  采纳
                </ElButton>
                <span v-else class="querylog-hint">无权限</span>
              </template>
            </ElTableColumn>
            <template #empty>
              <ElEmpty :image-size="60" description="暂无候选(需累积真实提问与采纳记录)" />
            </template>
          </ElTable>
        </ElTabPane>

        <!-- 零召回 -->
        <ElTabPane name="zero">
          <template #label>
            <span>零召回<ElBadge v-if="zeroRecall.length" :value="zeroRecall.length" type="warning" class="ml-2" /></span>
          </template>

          <ElAlert type="warning" :closable="false" show-icon class="mb-2">
            <template #title>
              问了但一个候选都没搜到 —— 别名缺失最直接的证据。它不需要用户反馈即可用，
              上线初期就该看这里。需人工判断该补给哪个资产，故不提供一键采纳。
            </template>
          </ElAlert>

          <ElTable v-loading="loading" :data="zeroRecall" size="small" border>
            <ElTableColumn prop="query" label="问法(归一化后)" min-width="240" />
            <ElTableColumn prop="hits" label="出现次数" width="110" align="right" />
            <ElTableColumn label="调用方" width="150">
              <template #default="{ row }">
                <ElTag
                  v-for="c in row.callers" :key="c"
                  size="small" effect="plain" class="mr-1"
                >
                  {{ c }}
                </ElTag>
                <span v-if="!row.callers?.length" class="querylog-hint">未记录</span>
              </template>
            </ElTableColumn>
          </ElTable>
          <ElEmpty v-if="!zeroRecall.length && !loading" :image-size="60" description="无零召回记录" />
        </ElTabPane>

        <!-- 裸 SQL 审计。★ 权限独立：含 SQL 原文，无权限时整个 Tab 不渲染
             （渲染一个空表格会看起来像"没有数据"，不像"你没权限"）-->
        <ElTabPane v-if="canSeeRawSql" name="rawsql">
          <template #label>
            <span>裸 SQL 审计</span>
          </template>

          <ElAlert type="warning" :closable="false" class="mb-2">
            <template #title>
              这里的 SQL 由 agent 自己编写、<b>未经口径审核</b>，其结果不代表权威口径。
              审计的用途是回答「谁在读哪张表」与「这条路有没有被滥用」。
            </template>
          </ElAlert>

          <div class="flex items-center gap-2 mb-2">
            <ElCheckbox v-model="rawSqlOnlyFailed" @change="reloadRawSqlLogs">
              只看失败的
            </ElCheckbox>
            <span class="querylog-hint">
              被守卫拒绝的调用也会留痕 —— 反复出现 permission_denied 说明有人在试探写操作
            </span>
          </div>

          <ElTable
            v-loading="loading"
            :data="rawSqlLogs"
            size="small"
            border
            :row-class-name="rawSqlRowClass"
          >
            <ElTableColumn prop="logged_at" label="时间" width="160" />
            <ElTableColumn prop="caller" label="调用方" width="110" />
            <ElTableColumn prop="raw_sql" label="SQL" min-width="260">
              <template #default="{ row }">
                <span class="raw-sql-cell" :title="row.raw_sql">{{ row.raw_sql }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="表" min-width="160">
              <template #default="{ row }">
                <ElTag v-for="t in row.tables" :key="t" size="small" class="mr-1">
                  {{ t }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="结果" width="130">
              <template #default="{ row }">
                <ElTag v-if="row.ok" type="success" size="small">成功</ElTag>
                <ElTag v-else type="danger" size="small">{{ row.error_type }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="row_count" label="行数" width="80" />
            <ElTableColumn prop="duration_ms" label="耗时(ms)" width="100" />
          </ElTable>
        </ElTabPane>
      </ElTabs>
    </ElCard>
  </div>
</template>

<script setup lang="ts">
import type { AliasCandidate, CallerStat, ZeroRecallItem } from "@/api/module_semantic/querylog";
import type { RawSqlLogItem } from "@/api/module_semantic/rawsqllog";

import { ElMessageBox } from "element-plus";

import QueryLogAPI from "@/api/module_semantic/querylog";
import { RawSqlLogAPI } from "@/api/module_semantic/rawsqllog";
import { useAuth } from "@/hooks/core/useAuth";

import { approveMessage, callerParam, candidateKey, removeCandidate, resolveNodeType } from "./candidates";

defineOptions({ name: "SemanticQueryLog" });

const { hasAuth } = useAuth();
const canApprove = computed(() => hasAuth("module_semantic:querylog:approve"));

const loading = ref(false);
const purging = ref(false);
/** 正在采纳的行 key —— 按行显示 loading，不锁整表 */
const approving = ref<string | null>(null);
const activeTab = ref("candidates");
const sinceDays = ref(30);
const minHits = ref(2);

const candidates = ref<AliasCandidate[]>([]);
const zeroRecall = ref<ZeroRecallItem[]>([]);

/** 裸 SQL 审计。★ 权限独立于 querylog —— 它含 SQL 原文 */
const canSeeRawSql = computed(() => hasAuth("module_semantic:rawsqllog:list"));
const rawSqlLogs = ref<RawSqlLogItem[]>([]);
const rawSqlOnlyFailed = ref(false);

/**
 * 取审计列表。
 *
 * ★ 与其它两个 load* 一样只吞异常、不动 `loading` —— 由调用方统一开关。
 * 自己开关 loading 会与并发的 refreshAll 抢同一个 ref：先结束的那个把 loading
 * 置回 false，另一个还在飞，界面看起来已经加载完但表格是旧数据。
 *
 * ★ 无权限时不发请求：否则每次刷新都撞一个 403，页面还会弹一次"暂无权限"。
 */
async function loadRawSqlLogs() {
  if (!canSeeRawSql.value) {
    rawSqlLogs.value = [];
    return;
  }
  try {
    const res = await RawSqlLogAPI.recent({
      since_days: sinceDays.value,
      caller: callerParam(callerFilter.value),
      only_failed: rawSqlOnlyFailed.value,
      limit: 100,
    });
    rawSqlLogs.value = res.data.data ?? [];
  } catch {
    rawSqlLogs.value = []; // 审计读失败不该整页崩
  }
}

/** 「只看失败的」勾选后单独重取 —— 不必连带重查候选/零召回 */
async function reloadRawSqlLogs() {
  loading.value = true;
  try {
    await loadRawSqlLogs();
  } finally {
    loading.value = false;
  }
}

/** 失败行标红 —— 排查滥用时要一眼看出来 */
function rawSqlRowClass({ row }: { row: RawSqlLogItem }) {
  return row.ok ? "" : "raw-sql-failed-row";
}

/**
 * 按调用方筛选。空串 = 全部。
 *
 * ★ 为什么要有它：自动巡检与真人提问的**采纳标准不同** —— 巡检脚本里写死的措辞
 * 被问一万次也不该采纳成别名，而真人反复这么叫才说明"用户就这么称呼它"。
 * 不区分调用方的话，挖出的候选无法判断该不该采纳。
 */
const callerFilter = ref("");
const callerOptions = ref<CallerStat[]>([]);

/** 行 key 走 candidates.ts 的三元组实现（同名不同资产必须区分开，见其单测） */
function rowKey(row: AliasCandidate): string {
  return candidateKey(row);
}

async function loadCandidates() {
  try {
    const res = await QueryLogAPI.aliasCandidates({
      since_days: sinceDays.value,
      min_hits: minHits.value,
      // 空选择转 undefined，理由见 callerParam 的注释（有单测）
      caller: callerParam(callerFilter.value),
    });
    candidates.value = res.data.data ?? [];
  } catch {
    candidates.value = []; // 治理页读失败不该整页崩
  }
}

async function loadZeroRecall() {
  try {
    const res = await QueryLogAPI.zeroRecall({
      since_days: sinceDays.value,
      caller: callerParam(callerFilter.value),
    });
    zeroRecall.value = res.data.data ?? [];
  } catch {
    zeroRecall.value = [];
  }
}

async function loadCallers() {
  try {
    const res = await QueryLogAPI.callers({ since_days: sinceDays.value });
    callerOptions.value = res.data.data ?? [];
  } catch {
    callerOptions.value = []; // 读不到就退化成"只能看全部"，不该让整页崩
  }
}

async function refreshAll() {
  loading.value = true;
  try {
    await Promise.all([loadCandidates(), loadZeroRecall(), loadCallers(), loadRawSqlLogs()]);
  } finally {
    loading.value = false;
  }
}

/**
 * 手动清理超期日志。
 *
 * ★ 用 ElMessageBox.prompt 让人**填保留天数**而不是给个固定按钮：清多少是要当场判断的
 * （表涨到多大、还想留多久回溯窗口），写死一个值等于替人做了决定。
 * 后端下界是 7 天 —— keep_days=0 会连刚写入的日志一起清掉。
 *
 * 说清是软删：这决定了人愿不愿意点。物删要犹豫，软删误清可恢复。
 */
async function handlePurge() {
  let keepDays: number;
  try {
    const { value } = await ElMessageBox.prompt(
      "清理早于「保留天数」的 query 日志。软删除，误清可恢复。\n"
      + "挖掘只回看 30–90 天，更久的日志对治理已无价值；留半年便于回溯"
      + "「某个问法是什么时候开始出现的」。",
      "清理超期日志",
      {
        inputValue: "180",
        inputPattern: /^\d+$/,
        // 下界跟后端一致(7)：填 0 会连今天刚写的日志一起清掉
        inputValidator: (v: string) => {
          const n = Number(v);
          if (!Number.isInteger(n) || n < 7 || n > 730) return "请填 7–730 之间的整数";
          return true;
        },
        inputPlaceholder: "保留天数(7–730)",
        confirmButtonText: "清理",
        cancelButtonText: "取消",
        type: "warning",
      },
    );
    keepDays = Number(value);
  } catch {
    return;
  }

  purging.value = true;
  try {
    const res = await QueryLogAPI.purge(keepDays);
    const data = res.data.data;
    // 0 条要单独说：报"已清理 0 条"看起来像失败，而它其实是"本来就没有超期的"
    if (data.purged) {
      ElMessage.success(`已清理 ${data.purged} 条（保留最近 ${data.keep_days} 天）`);
    } else {
      ElMessage.info(`没有早于 ${data.keep_days} 天的日志，无需清理`);
    }
    await refreshAll();
  } catch (e: any) {
    ElMessage.error(e?.message || "清理失败");
  } finally {
    purging.value = false;
  }
}

/**
 * 只重取清单 —— 换筛选条件不必重查调用方清单（它只随回看天数变）。
 *
 * ★ 审计也要跟着重取：它同样按 caller 过滤，不重取会让表格显示的还是上一个
 * 调用方的记录，而筛选框已经指向新的那个 —— 归因错误比没有筛选更糟。
 */
async function reloadLists() {
  loading.value = true;
  try {
    await Promise.all([loadCandidates(), loadZeroRecall(), loadRawSqlLogs()]);
  } finally {
    loading.value = false;
  }
}

/**
 * 逐条采纳。
 *
 * ★ 刻意没有"全部采纳"：人工审核的价值就在于逐条看过，批量按钮等于把这道闸拆了。
 * 采纳会改变检索行为（别名从活表取），所以先二次确认。
 */
async function handleApprove(row: AliasCandidate) {
  try {
    await ElMessageBox.confirm(
      `把「${row.alias}」作为 ${row.node_code} 的别名？采纳后立即生效(会同步进检索索引)。`,
      "确认采纳",
      { type: "warning", confirmButtonText: "采纳", cancelButtonText: "取消" },
    );
  } catch {
    return; // 用户取消
  }

  approving.value = rowKey(row);
  try {
    const res = await QueryLogAPI.approveAlias({
      alias: row.alias,
      node_type: resolveNodeType(row.node_type),
      node_code: row.node_code,
    });
    ElMessage.success(approveMessage(Boolean(res.data.data?.already_present)));
    // 采纳过的从候选里移除，避免重复点（只移除该三元组，同名不同资产的保留）
    candidates.value = removeCandidate(candidates.value, row);
  } catch (e: any) {
    ElMessage.error(e?.message || "采纳失败");
  } finally {
    approving.value = null;
  }
}

onMounted(refreshAll);
</script>

<style lang="scss" scoped>
.querylog-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  /* 标签绝不换行：被压窄时「调用方」会变成三行竖排（浏览器实测踩到） */
  white-space: nowrap;
}

.querylog-hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.raw-sql-cell {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--el-font-family-monospace, monospace);
  font-size: 12px;
}

:deep(.raw-sql-failed-row) {
  background-color: var(--el-color-danger-light-9);
}
</style>
