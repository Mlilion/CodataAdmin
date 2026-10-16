import { shallowMount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import type { TreeEntityNode } from "@/api/module_semantic/model";
import EntityGraphNode from "./EntityGraphNode.vue";

const entity: TreeEntityNode = {
  member_id: 1,
  role: "primary",
  sort: 0,
  id: 10,
  code: "order",
  name: "订单",
  entity_type: "fact",
  status: 2,
  completeness: { light: "green", missing: [] },
  fields: [
    {
      id: 11,
      code: "order_id",
      name: "订单编号",
      field_role: "id",
      completeness: { light: "green", missing: [] },
    },
    {
      id: 12,
      code: "pay_amount",
      name: "实付金额",
      field_role: "measure",
      completeness: { light: "yellow", missing: ["缺少单位"] },
    },
  ],
  dimensions: [
    {
      id: 21,
      code: "order_date",
      name: "下单日期",
      status: 2,
      completeness: { light: "green", missing: [] },
    },
  ],
  indicators: [
    {
      id: 31,
      code: "gmv",
      name: "GMV",
      indicator_type: "atomic",
      status: 2,
      completeness: { light: "green", missing: [] },
    },
  ],
  knowledge: [],
};

describe("EntityGraphNode", () => {
  it("renders a primary entity with grouped semantic assets and warnings", () => {
    const wrapper = shallowMount(EntityGraphNode, {
      props: { data: { entity } },
      global: {
        stubs: {
          Handle: true,
          FaSvgIcon: true,
        },
      },
    });

    expect(wrapper.text()).toContain("订单");
    expect(wrapper.text()).toContain("order");
    expect(wrapper.text()).toContain("主事实 · fact");
    expect(wrapper.text()).toContain("字段");
    expect(wrapper.text()).toContain("订单编号");
    expect(wrapper.text()).toContain("维度");
    expect(wrapper.text()).toContain("下单日期");
    expect(wrapper.text()).toContain("指标");
    expect(wrapper.text()).toContain("GMV");
    expect(wrapper.find(".asset-light--yellow").exists()).toBe(true);
  });
});
