import { describe, expect, it } from "vitest";

import type { McpEndpoint } from "@/api/module_semantic/mcptoken";

import { buildClientSnippet, needsProxyHint, URL_PLACEHOLDER } from "./endpoint";

const served: McpEndpoint = {
  url: "https://admin.example.com/mcp",
  source: "request",
  mounted: true,
  transport: "Streamable HTTP",
  auth_header: "Authorization: Bearer <token>",
};

describe("buildClientSnippet", () => {
  it("★ 地址与令牌在同一段里给全", () => {
    // 这是唯一能看到明文的时刻。只给 header 会逼人关掉弹窗去找 URL，
    // 而关掉就再也看不到明文了。
    const snippet = buildClientSnippet(served, "sk_abc123");

    expect(snippet).toContain("https://admin.example.com/mcp");
    expect(snippet).toContain("Bearer sk_abc123");
  });

  it("接入信息取不到时用占位符，不产出一段悄悄少了地址的配置", () => {
    const snippet = buildClientSnippet(null, "sk_abc123");

    expect(snippet).toContain(URL_PLACEHOLDER);
    // 关键：不能以换行开头 —— 那种配置看起来完整，粘出去连不上
    expect(snippet.startsWith("\n")).toBe(false);
  });
});

describe("needsProxyHint", () => {
  it("推导来源要提醒核对(反代后面会是内网地址)", () => {
    expect(needsProxyHint(served)).toBe(true);
  });

  it("显式配过就不再提醒 —— 那是噪声", () => {
    // 噪声会让人连该看的告警也一起忽略
    expect(needsProxyHint({ ...served, source: "config" })).toBe(false);
  });

  it("端点未挂载时不提醒代理，那时该说的是更严重的事", () => {
    // 未挂载有专门的红色告警。两条一起出现会分散注意力，
    // 而"地址可能不对"在"地址根本不服务"面前是次要问题。
    expect(needsProxyHint({ ...served, mounted: false })).toBe(false);
  });

  it("取不到接入信息时不提醒", () => {
    expect(needsProxyHint(null)).toBe(false);
  });
});
