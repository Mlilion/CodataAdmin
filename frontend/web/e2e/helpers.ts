import { expect, type Page } from "@playwright/test";

export const CRED = { username: "super", password: "123456" };

/** 登录并进入系统（Hash 路由 /web/#/login）。 */
export async function login(page: Page): Promise<void> {
  await page.goto("#/login");
  // 账号登录 Tab 默认选中；填用户名/密码
  await page.getByPlaceholder("请输入用户名").fill(CRED.username);
  await page.getByPlaceholder("请输入密码").fill(CRED.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  // 登录成功后离开登录页
  await expect(page).not.toHaveURL(/#\/login/, { timeout: 20_000 });
}

/** 直接导航到语义中心某页（hash 路由）。 */
export async function goSemantic(page: Page, path: string): Promise<void> {
  await page.goto(`#/semantic/${path}`);
  // 等待主内容区渲染（表格或卡片出现）
  await page.waitForLoadState("networkidle");
  await dismissTour(page);
}

/** 关闭可能出现的新手引导遮罩(el-tour)，否则会拦截后续点击。 */
export async function dismissTour(page: Page): Promise<void> {
  const tour = page.locator(".el-tour");
  if (await tour.count()) {
    await page.keyboard.press("Escape");
    // Escape 不生效时兜底点关闭图标
    const close = page.locator(".el-tour__close, .el-tour__closebtn").first();
    if (await tour.isVisible().catch(() => false)) {
      await close.click({ force: true }).catch(() => {});
    }
    await tour.waitFor({ state: "hidden", timeout: 5_000 }).catch(() => {});
  }
}

/** 截图到 e2e/.output 下。 */
export async function shot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: `e2e/.output/${name}.png`, fullPage: true });
}
