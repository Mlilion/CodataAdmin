import { request } from "@utils";

const API_PATH = "/semantic/entity";

const EntityAPI = {
  listEntity(query?: EntityPageQuery) {
    return request<ApiResponse<PageResult<EntityTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailEntity(id: number) {
    return request<ApiResponse<EntityTable>>({ url: `${API_PATH}/detail/${id}`, method: "get" });
  },

  createEntity(body: EntityForm) {
    return request<ApiResponse<EntityTable>>({ url: `${API_PATH}/create`, method: "post", data: body });
  },

  updateEntity(id: number, body: EntityForm) {
    return request<ApiResponse<EntityTable>>({ url: `${API_PATH}/update/${id}`, method: "put", data: body });
  },

  deleteEntity(body: number[]) {
    return request<ApiResponse>({ url: `${API_PATH}/delete`, method: "delete", data: body });
  },

  changeStatus(id: number, status: number) {
    return request<ApiResponse<EntityTable>>({
      url: `${API_PATH}/status/${id}`,
      method: "patch",
      data: { status },
    });
  },
};

export default EntityAPI;

export interface EntityPageQuery extends PageQuery {
  code?: string;
  name?: string;
  entity_type?: string;
  domain_code?: string;
  status?: number;
}

export interface SemField {
  id?: number;
  entity_id?: number;
  code: string;
  name: string;
  physical_column?: string;
  physical_column_id?: number;
  expr?: string;
  field_role: string;
  data_type?: string;
  unit?: string;
  enum_values?: string;
  visibility: string;
  description?: string;
}

export interface EntityTable extends BaseType {
  code: string;
  name: string;
  entity_type: string;
  description?: string;
  domain_code?: string;
  physical_datasource?: string;
  physical_database?: string;
  physical_table?: string;
  physical_table_id?: number;
  grain?: string;
  default_time_field?: string;
  owner_id?: number;
  status: number;
  fields: SemField[];
}

export interface EntityForm extends BaseFormType {
  code: string;
  name: string;
  entity_type: string;
  description?: string;
  domain_code?: string;
  physical_datasource?: string;
  physical_database?: string;
  physical_table?: string;
  physical_table_id?: number;
  grain?: string;
  default_time_field?: string;
  owner_id?: number;
  fields?: SemField[];
}
