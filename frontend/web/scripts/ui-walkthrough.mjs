/**
 * 这几轮改动的前端页面走查（一次性验证脚本，非测试资产）。
 *
 * 目的：单测与 vue-tsc 都不覆盖**渲染层** —— 样式错位、抽屉叠在弹窗下面、
 * 按钮点了没反应、控制台报错，这些只有真开浏览器才看得见。
 *
 * 用本机 Chrome（channel: "chrome"）而不是下载 playwright 的 chromium：
 * 后者要拉 ~150MB，而这只是一次性走查。
 *
 * 跑法：node scripts/ui-walkthrough.mjs
 */
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.UI_BASE || "http://localhost:5180/web";
const USER = process.env.UI_USER || "admin";
const PASS = process.env.UI_PASS || "123456";
const OUT = path.resolve("/tmp/ui-walk");

fs.mkdirSync(OUT, { recursive: true });

/** 收集到的问题，最后统一汇报 */
const problems = [];
const consoleErrors = [];

function note(step, msg) {
  problems.push(`[${step}] ${msg}`);
  console.log(`  ⚠️  ${msg}`);
}

/**
 * 关掉首次进入的新手引导（el-tour）与右上角通知。
 *
 * ★ el-tour 会铺一层带镂空的 SVG 遮罩，它**拦截所有 pointer 事件** ——
 * 按钮明明 visible+enabled，点击却一直重试到超时（调试时就卡在这里）。
 * 这也是真实用户第一次进来会遇到的东西，所以顺手验证它能被关掉。
 */
async function dismissOverlays(page) {
  for (const sel of [".el-tour__close", ".el-tour .el-tour__footer button", ".el-notification__closeBtn"]) {
    const loc = page.locator(sel);
    for (let n = await loc.count(); n > 0; n--) {
      await loc.first().click({ timeout: 1500 }).catch(() => {});
      await page.waitForTimeout(200);
    }
  }
  // 兜底：直接把遮罩从 DOM 摘掉（有些版本关不干净）
  await page.evaluate(() => {
    document.querySelectorAll(".el-tour, .el-tour__mask").forEach((e) => e.remove());
  }).catch(() => {});
  await page.waitForTimeout(200);
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false });
}

/** 等页面稳定：网络静默 + 一点缓冲给 Vue 渲染 */
async function settle(page, ms = 900) {
  // ★ 不用 networkidle：dev server 的 HMR 是长连接，网络永不静默 —— 会一直挂到超时。
  // 改成等 DOM 就绪 + 固定缓冲给 Vue 渲染。
  await page.waitForLoadState("domcontentloaded").catch(() => {});
  await page.waitForTimeout(ms);
  await dismissOverlays(page);
}

const run = async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();

  page.on("console", (m) => {
    if (m.type() === "error") {
      const t = m.text();
      // Vite HMR / favicon 之类的噪声不算
      if (/favicon|HMR|\[vite\]/i.test(t)) return;
      consoleErrors.push(t.slice(0, 300));
    }
  });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${String(e).slice(0, 300)}`));

  // ---------------------------------------------------------------- 登录
  console.log("\n== 登录 ==");
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await settle(page, 1500);

  // 「新版本已发布」的提示框会浮在右上角，先关掉免得挡住东西
  const closeNotif = page.locator(".el-notification__closeBtn");
  for (let i = await closeNotif.count(); i > 0; i--) {
    await closeNotif.first().click().catch(() => {});
    await page.waitForTimeout(200);
  }

  const userInput = page.locator('input[placeholder*="用户名"], input[name="username"]').first();
  if (await userInput.count()) {
    // 用 pressSequentially 而非 fill：fill 直接改 DOM value，某些实现下 v-model
    // 拿不到（没触发 input 事件），于是点登录时表单里其实是空的
    await userInput.click();
    await userInput.pressSequentially(USER, { delay: 30 });
    const pw = page.locator('input[type="password"]').first();
    await pw.click();
    await pw.pressSequentially(PASS, { delay: 30 });
    await page.waitForTimeout(300);
    // ★ 不能用 hasText:/登录/ —— 它会先匹配到「账号登录」这个 **tab**（role=tab），
    // 点了什么也不会发生（调试时卡了两轮才发现）。按提交按钮的语义定位。
    await page.locator('button[type="submit"], .login-submit, button.el-button--primary')
      .filter({ hasText: /登录|登 录/ }).first().click();
    // 登录要等后端一轮 + 菜单树拉取 + 路由动态注册
    await page.waitForURL((u) => !/login/i.test(u.hash || u.href), { timeout: 20000 }).catch(() => {});
    await settle(page, 2500);
  }
  const loggedIn = !/login/i.test(page.url());
  console.log(`  URL: ${page.url()} → ${loggedIn ? "已登录" : "仍在登录页"}`);
  if (!loggedIn) {
    note("登录", "登录失败，后续步骤无法进行");
    await shot(page, "00-login-failed");
    await browser.close();
    return;
  }
  await shot(page, "00-logged-in");

  // ---------------------------------------------------------------- 逐页走查
  const pages = [
    { key: "01-aiconfig", url: "/system/aiconfig", name: "AI 配置（rebuild 按钮 + 重建向量列）",
      expect: ["检索索引状态"] },
    { key: "02-mcptoken", url: "/semantic/mcp-token", name: "MCP 令牌（新页面）",
      expect: ["MCP 访问令牌"] },
    { key: "03-querylog", url: "/semantic/querylog", name: "同义词治理（caller 筛选 + 清理日志）",
      expect: ["同义词治理", "调用方"] },
    { key: "04-knowledge", url: "/semantic/knowledge", name: "知识库（来源/复核列 + 待复核清单）",
      expect: ["待复核清单"] },
    { key: "05-indicator", url: "/semantic/indicator", name: "指标（跨域不可算入口）",
      expect: ["跨域不可算指标"] },
    { key: "06-domain", url: "/semantic/domain", name: "业务域（批量启停）",
      expect: ["批量启用"] },
    { key: "07-model", url: "/semantic/model", name: "语义模型（授权管理 + 可见性标记）",
      expect: [] },
    { key: "13-upstreamchange", url: "/semantic/upstream-change", name: "上游变更（新页面）",
      expect: ["上游变更", "受影响"] },
  ];

  for (const p of pages) {
    console.log(`\n== ${p.name} ==`);
    const before = consoleErrors.length;
    await page.goto(`${BASE}#${p.url}`, { waitUntil: "domcontentloaded" });
    await settle(page, 1600);

    const body = await page.locator("body").innerText().catch(() => "");
    // 路由没配好会落到 404 页
    if (/404|页面不存在|Not Found/i.test(body.slice(0, 400))) {
      note(p.name, `路由未生效（落到 404）：${p.url}`);
    }
    for (const kw of p.expect) {
      if (!body.includes(kw)) note(p.name, `页面上找不到「${kw}」`);
    }
    const newErrs = consoleErrors.length - before;
    if (newErrs > 0) note(p.name, `控制台报了 ${newErrs} 条错误`);
    await shot(page, p.key);
    console.log(`  ✓ 截图 ${p.key}.png${newErrs ? ` (${newErrs} 控制台错误)` : ""}`);
  }

  // ---------------------------------------------------------------- 交互：打开各弹层
  console.log("\n== 交互走查 ==");

  // 待复核清单抽屉
  await page.goto(`${BASE}#/semantic/knowledge`, { waitUntil: "domcontentloaded" });
  await settle(page, 1600);
  const staleBtn = page.locator("button", { hasText: "待复核清单" }).first();
  if (await staleBtn.count()) {
    await staleBtn.click({ timeout: 8000 }).catch((e) => note("知识库", `点「待复核清单」失败: ${String(e).slice(0, 80)}`));
    await settle(page, 1200);
    // ★ 判据用 .el-drawer__body 的可见性 + 标题文字：.el-drawer 根节点常驻 DOM，
    // isVisible() 会误报未打开（实测抽屉开着、内容都渲染了却报未打开）
    const open = await page.locator(".el-drawer__body").first().isVisible().catch(() => false);
    console.log(`  待复核抽屉: ${open ? "打开" : "未打开"}`);
    if (!open) note("知识库", "点「待复核清单」抽屉没打开");
    await shot(page, "08-knowledge-stale-drawer");
    await page.keyboard.press("Escape");
    await settle(page, 600);
  } else {
    note("知识库", "找不到「待复核清单」按钮");
  }

  // 跨域不可算抽屉
  await page.goto(`${BASE}#/semantic/indicator`, { waitUntil: "domcontentloaded" });
  await settle(page, 1600);
  const crossBtn = page.locator("button", { hasText: "跨域不可算指标" }).first();
  if (await crossBtn.count()) {
    await crossBtn.click({ timeout: 8000 }).catch((e) => note("指标", `点「跨域不可算指标」失败: ${String(e).slice(0, 80)}`));
    await settle(page, 1400);
    const open = await page.locator(".el-drawer__body").first().isVisible().catch(() => false);
    const rows = await page.locator(".el-drawer .el-table__row").count().catch(() => 0);
    console.log(`  跨域抽屉: ${open ? "打开" : "未打开"} / 行数 ${rows}`);
    if (!open) note("指标", "点「跨域不可算指标」抽屉没打开");
    if (open && rows === 0) note("指标", "抽屉打开但没有数据行（后端有 8 条）");
    await shot(page, "09-indicator-cross-drawer");
    await page.keyboard.press("Escape");
    await settle(page, 600);
  } else {
    note("指标", "找不到「跨域不可算指标」按钮");
  }

  // MCP 令牌：签发弹窗
  await page.goto(`${BASE}#/semantic/mcp-token`, { waitUntil: "domcontentloaded" });
  await settle(page, 1600);
  const issueBtn = page.locator("button", { hasText: "签发令牌" }).first();
  if (await issueBtn.count()) {
    await issueBtn.click({ timeout: 8000 }).catch((e) => note("MCP 令牌", `点「签发令牌」失败: ${String(e).slice(0, 80)}`));
    await settle(page, 1000);
    const dlgText = await page.locator(".el-dialog").first().innerText().catch(() => "");
    const hasFields = ["调用方名", "绑定用户", "过期时间"].filter((k) => dlgText.includes(k));
    console.log(`  签发弹窗字段: ${hasFields.join(" / ") || "(空)"}`);
    if (hasFields.length < 3) note("MCP 令牌", `签发弹窗缺字段，只看到 ${hasFields.join("/") || "无"}`);
    await shot(page, "10-mcptoken-create-dialog");
  } else {
    note("MCP 令牌", "找不到「签发令牌」按钮");
  }

  // 治理页：调用方下拉
  await page.goto(`${BASE}#/semantic/querylog`, { waitUntil: "domcontentloaded" });
  await settle(page, 1600);
  const sel = page.locator(".el-select").first();
  if (await sel.count()) {
    await sel.click({ timeout: 8000 }).catch((e) => note("治理页", `点调用方下拉失败: ${String(e).slice(0, 80)}`));
    await settle(page, 900);
    const opts = await page.locator(".el-select-dropdown__item").allInnerTexts().catch(() => []);
    console.log(`  调用方下拉选项: ${opts.slice(0, 5).join(" | ") || "(空)"}`);
    if (!opts.some((o) => o.includes("全部"))) note("治理页", "调用方下拉里没有「全部」项");
    await shot(page, "11-querylog-caller-select");
  } else {
    note("治理页", "找不到调用方下拉");
  }

  await browser.close();

  // ---------------------------------------------------------------- 汇报
  console.log("\n" + "=".repeat(60));
  console.log(`截图目录: ${OUT}`);
  if (consoleErrors.length) {
    console.log(`\n控制台错误 ${consoleErrors.length} 条（去重后前 8）:`);
    [...new Set(consoleErrors)].slice(0, 8).forEach((e) => console.log(`  · ${e}`));
  } else {
    console.log("\n控制台: 无错误");
  }
  if (problems.length) {
    console.log(`\n发现 ${problems.length} 个问题:`);
    problems.forEach((p) => console.log(`  ✗ ${p}`));
    process.exitCode = 1;
  } else {
    console.log("\n✅ 所有页面与交互走查通过");
  }
};

run().catch((e) => {
  console.error("走查脚本自身失败:", e);
  process.exitCode = 2;
});
