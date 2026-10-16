import { request } from "@utils";

const API_PATH = "/semantic/mcp/token";

/**
 * 一个 MCP 接入方的令牌元信息。
 *
 * ★ 结构上**没有** token 字段 —— 库里存的是 SHA-256 哈希，明文只在签发/轮换时
 * 返回一次（见 TokenCreated）。所以列表接口不可能漏出明文。
 */
export interface McpToken {
  id: number;
  name: string;
  /** 明文前若干位，仅用于在列表里认出"这条是哪个 token" */
  token_prefix: string;
  /** 绑定用户名：模型级隔离的身份。留空则该 token 只能看 public 模型 */
  bound_username?: string | null;
  /** 最后一次使用时间。没有它没人敢删任何 token(不知还有没有人在用) */
  last_used_at?: string | null;
  expires_at?: string | null;
  revoked: boolean;
  /** 后端算好的派生态，前端别自己拿 expires_at 比当前时间(比错会显示成可用) */
  expired: boolean;
  active: boolean;
  description?: string | null;
  created_time?: string;
}

/** 签发/轮换的返回 —— 唯一带明文的出参 */
export interface TokenCreated extends McpToken {
  /** 明文令牌。**仅此一次可见**，之后任何接口都取不回 */
  token: string;
}

/**
 * MCP 接入信息 —— 接入方要填的那几行。
 *
 * ★ 结构上不含任何令牌字段：地址与钥匙是两回事。
 */
export interface McpEndpoint {
  /** 完整地址，可直接粘进 MCP client 配置 */
  url: string;
  /** `config`=由 SEM_MCP_PUBLIC_URL 指定；`request`=由当前请求推导 */
  source: "config" | "request";
  /** 此刻是否真在服务。给一个不服务的地址比不给更坏，故必须显示 */
  mounted: boolean;
  transport: string;
  /** 鉴权头示例，里面是占位符而非真令牌 */
  auth_header: string;
}

/** env 里配的令牌（只读，不可在页面吊销/轮换） */
export interface EnvToken {
  name: string;
  bound_username?: string | null;
  source: string;
}

export interface TokenForm {
  name: string;
  bound_username?: string | null;
  expires_at?: string | null;
  description?: string | null;
}

const McpTokenAPI = {
  /** 库里签发的令牌（含已吊销 —— 审计要看得到） */
  listToken() {
    return request<ApiResponse<McpToken[]>>({ url: `${API_PATH}/list`, method: "get" });
  },

  /**
   * env 里配的令牌（只列名称，不返回 token 本身）。
   *
   * 为什么要列：不然"当前到底有哪些 token 能进来"在页面上没有完整答案 ——
   * 只显示库里的会让人以为 env 那些已经不生效了，然后照着页面去查一个不存在的问题。
   */
  listEnvToken() {
    return request<ApiResponse<EnvToken[]>>({ url: `${API_PATH}/env`, method: "get" });
  },

  /**
   * 接入信息（地址/传输/鉴权头）。
   *
   * 为什么要有：光发令牌等于给了钥匙不给门牌号。文档里写的是
   * `http://<host>:<port>/mcp` 占位符，答不出"我这套部署填什么"。
   */
  getEndpoint() {
    return request<ApiResponse<McpEndpoint>>({ url: `${API_PATH}/endpoint`, method: "get" });
  },

  /** 签发。返回值里的明文只出现这一次 */
  createToken(body: TokenForm) {
    return request<ApiResponse<TokenCreated>>({ url: `${API_PATH}/create`, method: "post", data: body });
  },

  /** 改元信息（绑定用户/过期/备注）。换 token 本身走 rotate */
  updateToken(id: number, body: Partial<TokenForm>) {
    return request<ApiResponse<McpToken>>({ url: `${API_PATH}/update/${id}`, method: "put", data: body });
  },

  /** 轮换：旧的立刻失效、发一把新的（保留调用方名与绑定用户） */
  rotateToken(id: number) {
    return request<ApiResponse<TokenCreated>>({ url: `${API_PATH}/rotate/${id}`, method: "post" });
  },

  /** 吊销（保留行以留审计痕迹），立刻生效 */
  revokeToken(id: number) {
    return request<ApiResponse<McpToken>>({ url: `${API_PATH}/revoke/${id}`, method: "patch" });
  },

  /** 删除记录。日常应当用吊销 —— 出事后要查"谁在什么时候发过什么" */
  deleteToken(ids: number[]) {
    return request<ApiResponse>({ url: `${API_PATH}/delete`, method: "delete", data: ids });
  },
};

export default McpTokenAPI;
