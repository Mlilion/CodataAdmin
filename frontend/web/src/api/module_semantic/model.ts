import { request } from "@utils";

const API_PATH = "/semantic/model";

const ModelAPI = {
  listModel(query?: ModelPageQuery) {
    return request<ApiResponse<PageResult<SemModel>>>({ url: `${API_PATH}/list`, method: "get", params: query });
  },

  // ★ 刻意不封装 GET /detail/{id} 与 GET /member/list：
  // `tree(id)` 一次就返回 model 本体 + entities（含 member_id/role/sort）+ joins + 完整度，
  // 而模型页只有"选中一个模型"这一种进入方式、进来就要整棵树。
  // 单独留两个更细的封装无人调用，只会让人以为"这里还有一条数据来源"而去猜该用哪个。
  // 后端端点保留（供外部/脚本按需取用），前端不留死封装。

  createModel(body: ModelForm) {
    return request<ApiResponse<SemModel>>({ url: `${API_PATH}/create`, method: "post", data: body });
  },

  updateModel(id: number, body: Partial<ModelForm>) {
    return request<ApiResponse<SemModel>>({ url: `${API_PATH}/update/${id}`, method: "put", data: body });
  },

  deleteModel(body: number[]) {
    return request<ApiResponse>({ url: `${API_PATH}/delete`, method: "delete", data: body });
  },

  // ---- 成员 ----（读走 tree，见上方注释；这里只有写）
  addMember(modelId: number, body: MemberForm) {
    return request<ApiResponse<ModelMember>>({ url: `${API_PATH}/member/add/${modelId}`, method: "post", data: body });
  },

  updateMember(memberId: number, body: Partial<MemberForm>) {
    return request<ApiResponse>({ url: `${API_PATH}/member/update/${memberId}`, method: "put", data: body });
  },

  removeMember(body: number[]) {
    return request<ApiResponse>({ url: `${API_PATH}/member/delete`, method: "delete", data: body });
  },

  // ---- 用户授权（MCP 用户隔离；仅 visibility=restricted 时起作用）----
  //
  // 权限点是 module_semantic:model:grant，不是 model:update —— 授权是改访问控制，
  // 不该由"能编辑模型的人"顺带获得。

  listGrant(modelId: number) {
    return request<ApiResponse<ModelGrant[]>>({ url: `${API_PATH}/grant/list`, method: "get", params: { model_id: modelId } });
  },

  /** 授权给一批用户（幂等；收回过的会复活） */
  addGrant(modelId: number, userIds: number[]) {
    return request<ApiResponse<ModelGrant[]>>({ url: `${API_PATH}/grant/add/${modelId}`, method: "post", data: { user_ids: userIds } });
  },

  /** 收回授权。按 user_id 而非授权记录 id —— 语义是"别让这个人再看" */
  revokeGrant(modelId: number, userIds: number[]) {
    return request<ApiResponse>({ url: `${API_PATH}/grant/revoke/${modelId}`, method: "delete", data: userIds });
  },

  // ---- 树 ----
  tree(id: number) {
    return request<ApiResponse<ModelTree>>({ url: `${API_PATH}/tree/${id}`, method: "get" });
  },

  // ---- 发布 ----
  publish(id: number) {
    return request<ApiResponse<ModelRelease>>({ url: `${API_PATH}/publish/${id}`, method: "post" });
  },

  listRelease(modelId: number) {
    return request<ApiResponse<ModelRelease[]>>({ url: `${API_PATH}/release/list`, method: "get", params: { model_id: modelId } });
  },

  releaseDetail(id: number) {
    return request<ApiResponse<ModelReleaseDetail>>({ url: `${API_PATH}/release/detail/${id}`, method: "get" });
  },
};

export default ModelAPI;

export interface ModelPageQuery extends PageQuery {
  code?: string;
  name?: string;
  domain_id?: number;
  status?: number;
}

/** 可见性：public 所有 MCP 调用方可见 / restricted 仅 owner + 授权用户 + 超管 */
export type ModelVisibility = "public" | "restricted";

export interface SemModel {
  id: number;
  code: string;
  name: string;
  domain_id?: number;
  description?: string;
  default_time_grain?: string;
  version: number;
  status: number;
  owner_id?: number;
  visibility: ModelVisibility;
}

export interface ModelForm {
  code: string;
  name: string;
  domain_id?: number;
  description?: string;
  default_time_grain?: string;
  owner_id?: number;
  visibility?: ModelVisibility;
}

/** 一条授权记录（带用户名，否则页面上只有一串 id） */
export interface ModelGrant {
  id: number;
  model_id: number;
  user_id: number;
  username?: string;
  user_name?: string;
  created_time?: string;
}

export interface MemberForm {
  entity_id: number;
  role: string;
  sort?: number;
}

export interface ModelMember {
  id: number;
  model_id: number;
  entity_id: number;
  role: string;
  sort: number;
  entity_code?: string;
  entity_name?: string;
  entity_type?: string;
  entity_status?: number;
}

// ---- 树结构 ----
export interface Completeness {
  light: "green" | "yellow" | "red";
  missing: string[];
}

export interface TreeKnowledge {
  id: number;
  key: string;
  summary: string;
  knowledge_type: string;
  usage_mode: string;
  ref_type: string;
  status: number;
  completeness?: Completeness;
}

export interface TreeField {
  id: number;
  code: string;
  name: string;
  field_role?: string;
  data_type?: string;
  physical_column_id?: number;
  physical_column?: string;
  expr?: string;
  completeness?: Completeness;
}

export interface TreeDimension {
  id: number;
  code: string;
  name: string;
  dimension_type?: string;
  bind_field_code?: string;
  status: number;
  completeness?: Completeness;
}

export interface TreeIndicator {
  id: number;
  code: string;
  name: string;
  indicator_type: string;
  status: number;
  completeness?: Completeness;
}

export interface TreeJoin {
  id: number;
  from_entity_id: number;
  to_entity_id: number;
  from_entity_code?: string;
  to_entity_code?: string;
  on_expr: string;
  relationship_type: string;
  join_type: string;
  status: number;
  completeness?: Completeness;
}

export interface TreeEntityNode {
  member_id: number;
  role: string;
  sort: number;
  id: number;
  code: string;
  name: string;
  entity_type?: string;
  status: number;
  grain?: string;
  physical_table_id?: number;
  physical_datasource?: string;
  physical_database?: string;
  physical_table?: string;
  description?: string;
  completeness?: Completeness;
  fields: TreeField[];
  dimensions: TreeDimension[];
  indicators: TreeIndicator[];
  knowledge: TreeKnowledge[];
}

export interface ModelTree {
  model: SemModel;
  domain_name?: string;
  entities: TreeEntityNode[];
  model_knowledge: TreeKnowledge[];
  joins: TreeJoin[];
  model_completeness?: Completeness;
  publishable: boolean;
  publish_blockers: string[];
}

// ---- 发布版本 ----
export interface ModelRelease {
  id: number;
  model_id: number;
  version: number;
  node_stats?: string;
  checksum?: string;
  created_time?: string;
}

export interface ModelReleaseDetail extends ModelRelease {
  snapshot: string;
}
