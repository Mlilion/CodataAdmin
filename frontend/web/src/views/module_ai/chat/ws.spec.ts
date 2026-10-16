import { describe, expect, it } from "vitest";

import { buildChatWebSocketUrl } from "./ws";

describe("chat websocket URL", () => {
  it("uses the current page origin for a relative production endpoint", () => {
    expect(
      buildChatWebSocketUrl("/", {
        protocol: "https:",
        host: "codata.example.com",
      })
    ).toBe("wss://codata.example.com/api/v1/ai/chat/ws");
  });

  it("keeps an explicitly configured websocket endpoint", () => {
    expect(
      buildChatWebSocketUrl("ws://127.0.0.1:8001", {
        protocol: "http:",
        host: "localhost:5180",
      })
    ).toBe("ws://127.0.0.1:8001/api/v1/ai/chat/ws");
  });
});
