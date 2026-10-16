import { request } from "@utils";

const API_PATH = "/semantic/dimension";

const DimensionAPI = {
  listDimension(query?: DimensionPageQuery) {
    return request<ApiResponse<PageResult<DimensionTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailDimension(id: number) {
    return request<ApiResponse<DimensionTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createDimension(body: DimensionForm) {
    return request<ApiResponse<DimensionTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateDimension(id: number, body: DimensionForm) {
    return request<ApiResponse<DimensionTable>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteDimension(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  changeStatus(id: number, status: number) {
    return request<ApiResponse<DimensionTable>>({
      url: `${API_PATH}/status/${id}`,
      method: "patch",
      data: { status },
    });
  },
};

export default DimensionAPI;

export interface DimensionPageQuery extends PageQuery {
  code?: string;
  name?: string;
  dimension_type?: string;
  status?: number;
}

export interface DimensionTable extends BaseType {
  code: string;
  name: string;
  dimension_type?: string;
  description?: string;
  aliases?: string;
  bind_entity_id?: number;
  bind_field_code?: string;
  status: number;
}

export interface DimensionForm extends BaseFormType {
  code: string;
  name: string;
  dimension_type?: string;
  description?: string;
  aliases?: string;
  bind_entity_id?: number;
  bind_field_code?: string;
}
