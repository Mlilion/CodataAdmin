import { expect, test } from "@playwright/test";
import { goSemantic, login, shot } from "./helpers";

// 语义中心 8 页(component_path 后缀 → 菜单路由)
const PAGES = [
  { path: "catalog", title: "物理目录" },
  { path: "domain", title: "业务域" },
  { path: "entity", title: "业务实体" },
  { path: "model", title: "语义模型" },
  { path: "join", title: "JOIN 关系" },
  { path: "indicator", title: "指标管理" },
  { path: "dimension", title: "维度管理" },
  { path: "knowledge", title: "知识库" },
];

test.describe("语义中心 - 冒烟(8页可打开不白屏)", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  for (const p of PAGES) {
    test(`打开 ${p.title}`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await goSemantic(page, p.path);
      // 等待视图内容区真正挂载(排除白屏):卡片/表格/目录容器出现
      const content = page.locator(".el-card, .el-table, .catalog-page, .join-graph").first();
      await expect(content, `${p.title} 应渲染出内容区`).toBeVisible({ timeout: 15_000 });
      await shot(page, `smoke-${p.path}`);
      expect(errors, `${p.title} 不应有页面级 JS 错误`).toHaveLength(0);
    });
  }
});

test.describe("语义中心 - 模型树(UI)", () => {
  // 依赖 API 冒烟数据: 模型「交易分析」+ 实体「订单」(backend P1 冒烟已建)
  test("树渲染出模型根与实体节点", async ({ page }) => {
    await login(page);
    await goSemantic(page, "model");
    // 左栏模型列表出现
    await expect(page.locator(".model-item").first()).toBeVisible({ timeout: 15_000 });
    // 树挂载且根节点=模型名
    const treeEl = page.locator(".el-tree");
    await expect(treeEl).toBeVisible({ timeout: 15_000 });
    await expect(treeEl).toContainText("交易分析");
    await expect(treeEl).toContainText("订单");
    // 完整度灯已渲染(P2: 每节点 🟢🟡🔴)
    await expect(page.locator(".light-dot").first()).toBeVisible();
    await shot(page, "model-tree");
  });
});

test.describe("语义中心 - 物理目录数据源管理(UI)", () => {
  test("数据源管理对话框可打开并渲染列表", async ({ page }) => {
    await login(page);
    await goSemantic(page, "catalog");
    await page.getByRole("button", { name: "数据源管理" }).click();
    // 对话框出现 + 表格挂载 + "登记数据源"按钮可见
    const dialog = page.locator(".el-dialog").filter({ hasText: "数据源管理" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    await expect(dialog.locator(".el-table")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "登记数据源" })).toBeVisible();
    await shot(page, "datasource-manage-dialog");
    // 登记表单可打开(校验关键字段)
    await dialog.getByRole("button", { name: "登记数据源" }).click();
    const form = page.locator(".el-dialog").filter({ hasText: "登记数据源" }).last();
    await expect(form.getByPlaceholder("便于识别的数据源名称")).toBeVisible();
    await shot(page, "datasource-create-form");
  });
});

test.describe("语义中心 - 指标完整流程(CRUD+多口径+状态流转)", () => {
  const CODE = `e2e_pw_${Date.now()}`;
  const API = "http://localhost:8001/api/v1";

  // 通过登录态调后端完成 CRUD+状态机(真实前后端栈),再用 UI 验证列表渲染。
  // (纯 UI 驱动 Element Plus 对话框/下拉过于脆弱,此处用 API 驱动业务、UI 验证呈现。)
  test("创建(多口径)→UI列表可见→提审→审核口径→发布→删除", async ({ page, request }) => {
    await login(page);

    // 取登录 token(固定键 access_token,存 localStorage 或 sessionStorage)
    const token = await page.evaluate(
      () => localStorage.getItem("access_token") || sessionStorage.getItem("access_token") || ""
    );
    expect(token, "应能从登录态取到 token").toBeTruthy();
    const h = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

    // 1. 创建指标(带一条主口径)
    const created = await request.post(`${API}/semantic/indicator/create`, {
      headers: h,
      data: {
        code: CODE,
        name: "E2EPlaywright指标",
        indicator_type: "atomic",
        impls: [{ role: "primary", sql_text: "SELECT SUM(amount) FROM orders", data_layer: "DWS" }],
      },
    });
    expect(created.ok(), "创建应成功").toBeTruthy();
    const ind = (await created.json()).data;
    const implId = ind.impls[0].id;

    // 2. UI 列表能搜到该编码(验证真实渲染)
    await goSemantic(page, "indicator");
    await page.getByPlaceholder(/请输入编码|编码/).first().fill(CODE);
    await page.getByRole("button", { name: "搜索", exact: true }).first().click().catch(() => {});
    await expect(page.getByText(CODE).first()).toBeVisible({ timeout: 15_000 });
    await shot(page, "flow-list-visible");

    // 3. 提审 → 审核口径通过 → 发布(状态机 + 发布守卫)
    const submit = await request.patch(`${API}/semantic/indicator/status/${ind.id}`, { headers: h, data: { lifecycle_status: 1 } });
    expect(submit.ok()).toBeTruthy();
    const review = await request.patch(`${API}/semantic/indicator/impl/review/${implId}`, { headers: h, data: { review_status: 1 } });
    expect(review.ok()).toBeTruthy();
    const publish = await request.patch(`${API}/semantic/indicator/status/${ind.id}`, { headers: h, data: { lifecycle_status: 2 } });
    expect((await publish.json()).data.lifecycle_status, "应发布成功(状态=2)").toBe(2);

    // 4. 清理
    const del = await request.delete(`${API}/semantic/indicator/delete`, { headers: h, data: [ind.id] });
    expect(del.ok()).toBeTruthy();
  });
});
