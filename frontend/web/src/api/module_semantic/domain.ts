import { request } from "@utils";

const API_PATH = "/semantic/domain";

const DomainAPI = {
  listDomain(query?: DomainPageQuery) {
    return request<ApiResponse<PageResult<DomainTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailDomain(id: number) {
    return request<ApiResponse<DomainTable>>({ url: `${API_PATH}/detail/${id}`, method: "get" });
  },

  createDomain(body: DomainForm) {
    return request<ApiResponse<DomainTable>>({ url: `${API_PATH}/create`, method: "post", data: body });
  },

  updateDomain(id: number, body: DomainForm) {
    return request<ApiResponse<DomainTable>>({ url: `${API_PATH}/update/${id}`, method: "put", data: body });
  },

  deleteDomain(body: number[]) {
    return request<ApiResponse>({ url: `${API_PATH}/delete`, method: "delete", data: body });
  },

  batchDomain(body: { ids: number[]; status: number }) {
    return request<ApiResponse>({ url: `${API_PATH}/status/batch`, method: "patch", data: body });
  },
};

export default DomainAPI;

export interface DomainPageQuery extends PageQuery {
  code?: string;
  name?: string;
  status?: number;
}

export interface DomainTable extends BaseType {
  code: string;
  name: string;
  order: number;
  description?: string;
  status: number;
}

export interface DomainForm extends BaseFormType {
  code: string;
  name: string;
  order?: number;
  description?: string;
}
