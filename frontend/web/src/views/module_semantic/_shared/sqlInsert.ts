/** 文本插入辅助:把物理列名插入目标 textarea 的光标处 */

export function insertAtCursor(
  text: string,
  start: number,
  end: number,
  insert: string
): { value: string; caret: number } {
  const t = text ?? "";
  const s = Math.max(0, Math.min(start, t.length));
  const e = Math.max(s, Math.min(end, t.length));
  return { value: t.slice(0, s) + insert + t.slice(e), caret: s + insert.length };
}

/** 兼容 ElInput 组件实例( .$el )与原生节点,解析出内部 textarea */
export function resolveTextarea(el: unknown): HTMLTextAreaElement | null {
  if (!el) return null;
  const node = (el as { $el?: unknown }).$el ?? el;
  if (node instanceof HTMLTextAreaElement) return node;
  const found = (node as HTMLElement | null)?.querySelector?.("textarea");
  return found ?? null;
}
