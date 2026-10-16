/**
 * 前端关键模块烟雾测试
 * 验证运行时枚举、类型定义的完整性和正确性。
 *
 * 注意：const enum（ThemeMode/DeviceEnum/LayoutMode/ResultEnum 等）
 * 在 TypeScript isolatedModules 模式下会被内联为常量，无法在运行时访问。
 * MenuTypeEnum 为常规 enum，下列代码生成枚举也为常规 enum。
 */

import { describe, it, expect } from "vitest";

// ══════════════════ 菜单类型枚举 ══════════════════
describe("MenuTypeEnum — 菜单类型", () => {
  it("should define 4 menu types with correct values", async () => {
    const { MenuTypeEnum } = await import("@/enums/system/menu.enum");
    expect(MenuTypeEnum.CATALOG).toBe(1);
    expect(MenuTypeEnum.MENU).toBe(2);
    expect(MenuTypeEnum.BUTTON).toBe(3);
    expect(MenuTypeEnum.EXTLINK).toBe(4);
  });
});

// ══════════════════ 代码生成枚举 ══════════════════
describe("代码生成枚举 — 完整性", () => {
  it("FormRuleType should be importable", async () => {
    const mod = await import("@/enums/codegen/form.enum");
    expect(mod).toBeDefined();
  });

  it("QueryRuleType should be importable", async () => {
    const mod = await import("@/enums/codegen/query.enum");
    expect(mod).toBeDefined();
  });
});
