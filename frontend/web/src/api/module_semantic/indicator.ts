import { request } from "@utils";

const API_PATH = "/semantic/indicator";

const IndicatorAPI = {
  listIndicator(query?: IndicatorPageQuery) {
    return request<ApiResponse<PageResult<IndicatorTable>>>({
      url: `${API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailIndicator(id: number) {
    return request<ApiResponse<IndicatorTable>>({
      url: `${API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createIndicator(body: IndicatorForm) {
    return request<ApiResponse<IndicatorTable>>({
      url: `${API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateIndicator(id: number, body: IndicatorForm) {
    return request<ApiResponse<IndicatorTable>>({
      url: `${API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteIndicator(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  changeStatus(id: number, lifecycle_status: number) {
    return request<ApiResponse<IndicatorTable>>({
      url: `${API_PATH}/status/${id}`,
      method: "patch",
      data: { lifecycle_status },
    });
  },

  reviewImpl(implId: number, review_status: number) {
    return request<ApiResponse>({
      url: `${API_PATH}/impl/review/${implId}`,
      method: "patch",
      data: { review_status },
    });
  },

  /**
   * 跨域不可算指标清单（无主实体）。
   *
   * ★ 必须有独立入口：这类指标 `primary_entity_id` 为空，模型树按成员过滤时
   * **根本不加载它们**，快照也不含 —— 不给入口就等于录了一批看不见的数据。
   * 它们的价值在边界拒答：agent 命中后能说"本模型不可算 + 正确出处"，
   * 而不是编造或硬 JOIN。让人也能看到这份声明。
   */
  crossDomain() {
    return request<ApiResponse<IndicatorTable[]>>({
      url: `${API_PATH}/cross-domain`,
      method: "get",
    });
  },
};

export default IndicatorAPI;

export interface IndicatorPageQuery extends PageQuery {
  code?: string;
  name?: string;
  indicator_type?: string;
  domain?: string;
  lifecycle_status?: number;
}

export interface IndicatorImpl {
  id?: number;
  indicator_id?: number;
  role: string;
  sql_text: string;
  label?: string;
  source_note?: string;
  data_layer?: string;
  granularity?: string;
  review_status?: number;
  reviewed_id?: number;
  reviewed_time?: string;
}

export interface IndicatorTable extends BaseType {
  code: string;
  name: string;
  aliases?: string;
  description?: string;
  business_definition?: string;
  indicator_type: string;
  domain?: string;
  business_process?: string;
  measure_expression?: string;
  unit?: string;
  data_type?: string;
  additivity?: string;
  decimal_places: number;
  biz_owner_id?: number;
  tech_owner_id?: number;
  business_importance?: number;
  primary_entity_id?: number;
  available_dimension_ids?: string; // JSON 数组字符串，如 "[3,7]"
  lifecycle_status: number;
  impls: IndicatorImpl[];
}

export interface IndicatorForm extends BaseFormType {
  code: string;
  name: string;
  aliases?: string;
  description?: string;
  business_definition?: string;
  indicator_type: string;
  domain?: string;
  business_process?: string;
  measure_expression?: string;
  unit?: string;
  data_type?: string;
  additivity?: string;
  decimal_places?: number;
  biz_owner_id?: number;
  tech_owner_id?: number;
  business_importance?: number;
  primary_entity_id?: number;
  available_dimension_ids?: string; // JSON 数组字符串，如 "[3,7]"
  impls?: IndicatorImpl[];
}
