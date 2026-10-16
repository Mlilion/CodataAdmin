/**
 * 接入信息的纯函数部分（拆出来是为了能单测 —— 组件里塞 computed 测不到分支）。
 */
import type { McpEndpoint } from "@/api/module_semantic/mcptoken";

/** 取不到接入信息时的占位符。与文档 `docs/semantic-mcp.md` 里的写法一致 */
export const URL_PLACEHOLDER = "http://<host>:<port>/mcp";

/**
 * 签发弹窗里那段可直接抄走的配置。
 *
 * ★ 地址与令牌必须在同一段里给全：这是**唯一**能看到明文的时刻，
 * 只给 header 会逼人关掉弹窗去别处找 URL —— 而关掉就再也看不到明文了。
 *
 * 地址取不到时用占位符而非留空：留空会产出一段看起来完整、
 * 实际连不上的配置（`\nAuthorization: ...`），那比明显的占位符更坏。
 */
export function buildClientSnippet(endpoint: McpEndpoint | null, token: string): string {
  const url = endpoint?.url || URL_PLACEHOLDER;
  return `${url}\nAuthorization: Bearer ${token}`;
}

/**
 * 是否要提醒"这个地址可能不是接入方可达的地址"。
 *
 * 只在**推导**来源时提醒：显式配过 `SEM_MCP_PUBLIC_URL` 的人已经做过这个决定，
 * 再提醒一遍是噪声，而噪声会让人连该看的告警也一起忽略。
 */
export function needsProxyHint(endpoint: McpEndpoint | null): boolean {
  return endpoint?.source === "request" && endpoint.mounted;
}
