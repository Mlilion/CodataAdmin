interface PageLocation {
  protocol: string;
  host: string;
}

const currentPageLocation = (): PageLocation => ({
  protocol: window.location.protocol,
  host: window.location.host,
});

export function buildChatWebSocketUrl(
  endpoint: string | undefined,
  pageLocation: PageLocation = currentPageLocation()
): string {
  const configuredEndpoint = endpoint?.trim();
  const baseUrl =
    configuredEndpoint && configuredEndpoint !== "/"
      ? configuredEndpoint
      : `${pageLocation.protocol === "https:" ? "wss:" : "ws:"}//${pageLocation.host}`;

  return new URL("/api/v1/ai/chat/ws", baseUrl).toString();
}
