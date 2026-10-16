import { request } from "@utils";

const API_PATH = "/semantic/join";

const JoinAPI = {
  listJoin(query?: JoinPageQuery) {
    return request<ApiResponse<PageResult<JoinTable>>>({ url: `${API_PATH}/list`, method: "get", params: query });
  },

  graph() {
    return request<ApiResponse<JoinGraph>>({ url: `${API_PATH}/graph`, method: "get" });
  },

  detailJoin(id: number) {
    return request<ApiResponse<JoinTable>>({ url: `${API_PATH}/detail/${id}`, method: "get" });
  },

  createJoin(body: JoinForm) {
    return request<ApiResponse<JoinTable>>({ url: `${API_PATH}/create`, method: "post", data: body });
  },

  updateJoin(id: number, body: JoinForm) {
    return request<ApiResponse<JoinTable>>({ url: `${API_PATH}/update/${id}`, method: "put", data: body });
  },

  deleteJoin(body: number[]) {
    return request<ApiResponse>({ url: `${API_PATH}/delete`, method: "delete", data: body });
  },

  changeStatus(id: number, status: number) {
    return request<ApiResponse<JoinTable>>({ url: `${API_PATH}/status/${id}`, method: "patch", data: { status } });
  },
};

export default JoinAPI;

export interface JoinPageQuery extends PageQuery {
  from_entity_id?: number;
  to_entity_id?: number;
  status?: number;
}

export interface JoinTable extends BaseType {
  from_entity_id: number;
  from_entity?: { id: number; name: string };
  to_entity_id: number;
  to_entity?: { id: number; name: string };
  on_expr: string;
  relationship_type: string;
  join_type: string;
  description?: string;
  status: number;
}

export interface JoinForm extends BaseFormType {
  from_entity_id?: number;
  to_entity_id?: number;
  on_expr: string;
  relationship_type: string;
  join_type: string;
  description?: string;
}

export interface JoinGraphNode {
  id: number;
  code: string;
  name: string;
  entity_type: string;
}

export interface JoinGraphEdge {
  id: number;
  from_entity_id: number;
  to_entity_id: number;
  relationship_type: string;
  on_expr: string;
}

export interface JoinGraph {
  nodes: JoinGraphNode[];
  edges: JoinGraphEdge[];
}
