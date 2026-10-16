import { describe, expect, test } from "vitest";
import { insertAtCursor, resolveTextarea } from "./sqlInsert";

describe("insertAtCursor", () => {
  test("有选区时替换选区", () => {
    const r = insertAtCursor("SUM(xxx)", 4, 7, "amt");
    expect(r.value).toBe("SUM(amt)");
    expect(r.caret).toBe(7);
  });
  test("无选区时在光标处插入", () => {
    const r = insertAtCursor("SUM()", 4, 4, "amt");
    expect(r.value).toBe("SUM(amt)");
    expect(r.caret).toBe(7);
  });
  test("越界位置被收敛", () => {
    const r = insertAtCursor("ab", 99, 99, "c");
    expect(r.value).toBe("abc");
  });
});

describe("resolveTextarea", () => {
  test("原生 textarea 直接返回", () => {
    const ta = document.createElement("textarea");
    expect(resolveTextarea(ta)).toBe(ta);
  });
  test("ElInput 实例经 $el 查询", () => {
    const ta = document.createElement("textarea");
    const wrap = document.createElement("div");
    wrap.appendChild(ta);
    expect(resolveTextarea({ $el: wrap })).toBe(ta);
  });
  test("空值返回 null", () => {
    expect(resolveTextarea(undefined)).toBeNull();
    expect(resolveTextarea(null)).toBeNull();
  });
});
