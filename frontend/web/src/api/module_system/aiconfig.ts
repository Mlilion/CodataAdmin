import { request } from "@utils";

const API_PATH = "/semantic/aiconfig";

/** 嵌入模型配置（平台级单例，向量检索用） */
export interface AiConfigForm {
  provider: string;
  base_url: string;
  /** 留空表示不修改（后端不会覆盖已存的 key） */
  api_key: string;
  model: string;
  dimensions: number;
  enabled: boolean;
  description?: string | null;
}

export interface AiConfigDetail {
  provider: string;
  base_url: string;
  /** 掩码，仅末四位可见 */
  api_key_masked: string;
  api_key_configured: boolean;
  model: string;
  dimensions: number;
  enabled: boolean;
  description?: string | null;
}

export interface AiConfigSaveResult {
  dimension_changed: boolean;
  old_dimensions?: number | null;
  new_dimensions?: number | null;
  /** 维度变更时被清空的向量数（将自动重算） */
  cleared: number;
}

export interface AiConfigTestResult {
  ok: boolean;
  dimensions?: number;
  model?: string;
  error?: string;
}

export interface RebuildResult {
  skipped?: boolean;
  cleared?: number;
  dimensions?: number;
  error?: string;
}

const AiConfigAPI = {
  detail() {
    return request<ApiResponse<AiConfigDetail>>({
      url: `${API_PATH}/detail`,
      method: "get",
    });
  },

  update(data: AiConfigForm) {
    return request<ApiResponse<AiConfigSaveResult>>({
      url: `${API_PATH}/update`,
      method: "put",
      data,
    });
  },

  /**
   * 试调端点并核对维度（维度不符是最常见的配错）。
   * 传 data 时测**表单里的当前值**（不必先保存）；api_key 留空则沿用已存的密钥。
   */
  test(data?: AiConfigForm) {
    return request<ApiResponse<AiConfigTestResult>>({
      url: `${API_PATH}/test`,
      method: "post",
      data,
    });
  },

  /** 手工重建向量列（维度变更时会自动做，此处供补救） */
  rebuildVectors() {
    return request<ApiResponse<RebuildResult>>({
      url: `${API_PATH}/rebuild-vectors`,
      method: "post",
    });
  },
};

export default AiConfigAPI;
