import { shallowMount } from "@vue/test-utils";
import { VueFlow } from "@vue-flow/core";
import { describe, expect, it } from "vitest";
import type { ModelTree, SemModel } from "@/api/module_semantic/model";
import SemanticModelWorkbench from "./SemanticModelWorkbench.vue";

const model: SemModel = {
  id: 10,
  code: "trade_analysis",
  name: "交易分析",
  description: "面向交易链路的日常经营分析",
  version: 3,
  status: 2,
  visibility: "public",
};

const tree: ModelTree = {
  model,
  domain_name: "交易域",
  entities: [
    {
      member_id: 1,
      role: "primary",
      sort: 0,
      id: 1,
      code: "order",
      name: "订单",
      entity_type: "fact",
      status: 2,
      completeness: { light: "green", missing: [] },
      fields: [],
      dimensions: [],
      indicators: [],
      knowledge: [],
    },
    {
      member_id: 2,
      role: "related",
      sort: 1,
      id: 2,
      code: "user",
      name: "用户",
      entity_type: "dimension",
      status: 2,
      completeness: { light: "yellow", missing: ["缺少负责人"] },
      fields: [],
      dimensions: [],
      indicators: [],
      knowledge: [],
    },
  ],
  model_knowledge: [],
  joins: [],
  model_completeness: { light: "yellow", missing: ["用户缺少负责人"] },
  publishable: true,
  publish_blockers: [],
};

function mountWorkbench(overrides: Record<string, unknown> = {}) {
  return shallowMount(SemanticModelWorkbench, {
    props: {
      tree,
      models: [model],
      domains: [],
      currentModelId: 10,
      treeLoading: false,
      publishing: false,
      treeData: [],
      expandedKeys: [],
      canCreate: true,
      canUpdate: true,
      canDelete: true,
      canGrant: true,
      ...overrides,
    },
    global: {
      stubs: {
        FaSvgIcon: true,
        Background: true,
        MiniMap: true,
        Controls: true,
        EntityGraphNode: true,
      },
    },
  });
}

describe("SemanticModelWorkbench", () => {
  it("renders the canvas-first model header, view switch and status summary", () => {
    const wrapper = mountWorkbench();

    expect(wrapper.text()).toContain("交易分析");
    expect(wrapper.text()).toContain("模型完整度");
    expect(wrapper.text()).toContain("关系图");
    expect(wrapper.text()).toContain("结构树");
    expect(wrapper.text()).toContain("发布版本");
    expect(wrapper.text()).toContain("2 个实体");
    expect(wrapper.text()).toContain("布局调整仅在当前页面生效");
  });

  it("caps canvas zoom so a single entity card is not oversized", () => {
    const wrapper = mountWorkbench();

    expect(wrapper.findComponent(VueFlow).props("maxZoom")).toBe(1);
  });

  it("recreates the graph when automatic layout is requested", async () => {
    const wrapper = mountWorkbench();

    expect(wrapper.findComponent(VueFlow).attributes("data-layout-version")).toBe("0");
    await wrapper.get('[data-testid="auto-layout"]').trigger("click");

    expect(wrapper.findComponent(VueFlow).attributes("data-layout-version")).toBe("1");
  });

  it("toggles fullscreen maximize on the relationship canvas", async () => {
    const wrapper = mountWorkbench();

    // 默认非全屏
    expect(wrapper.get(".relationship-canvas").classes()).not.toContain(
      "relationship-canvas--fullscreen"
    );

    // 点击全屏按钮 → 进入全屏
    await wrapper.get('[data-testid="toggle-fullscreen"]').trigger("click");
    expect(wrapper.get(".relationship-canvas").classes()).toContain(
      "relationship-canvas--fullscreen"
    );

    // 再次点击 → 退出全屏
    await wrapper.get('[data-testid="toggle-fullscreen"]').trigger("click");
    expect(wrapper.get(".relationship-canvas").classes()).not.toContain(
      "relationship-canvas--fullscreen"
    );
  });

  it("exits fullscreen when Escape is pressed", async () => {
    const wrapper = mountWorkbench();

    await wrapper.get('[data-testid="toggle-fullscreen"]').trigger("click");
    expect(wrapper.get(".relationship-canvas").classes()).toContain(
      "relationship-canvas--fullscreen"
    );

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".relationship-canvas").classes()).not.toContain(
      "relationship-canvas--fullscreen"
    );
  });

  it("only shows the fullscreen toggle in graph view", async () => {
    const wrapper = mountWorkbench();

    expect(wrapper.find('[data-testid="toggle-fullscreen"]').exists()).toBe(true);
  });

  it("opens and closes the inspector overlay after a graph selection", async () => {
    const wrapper = mountWorkbench();

    wrapper.findComponent(VueFlow).vm.$emit("node-click", {
      node: { data: { entity: tree.entities[0] } },
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".model-inspector").classes()).toContain("model-inspector--open");

    await wrapper.get('[data-testid="close-inspector"]').trigger("click");
    expect(wrapper.get(".model-inspector").classes()).not.toContain("model-inspector--open");
  });

  it("hides modifying actions without update and delete permissions", () => {
    const wrapper = mountWorkbench({ canUpdate: false, canDelete: false });

    expect(wrapper.text()).not.toContain("发布版本");
    expect(wrapper.find('[title="删除模型"]').exists()).toBe(false);
  });

  it("emits the selected model id from the model rail", async () => {
    const wrapper = mountWorkbench();

    await wrapper.get(".model-list-item").trigger("click");
    expect(wrapper.emitted("select-model")).toEqual([[10]]);
  });

  it("emits fix-node with the target for a missing check that has a fix target", async () => {
    const wrapper = mountWorkbench({
      selected: {
        title: "维度 · 城市",
        nodeType: "dimension",
        items: [],
        light: "red",
        missing: ["缺少绑定字段"],
        fixTarget: { type: "dimension", id: 42 },
      },
    });

    await wrapper.get(".check-card__fix").trigger("click");
    expect(wrapper.emitted("fix-node")).toEqual([[{ type: "dimension", id: 42 }]]);
  });

  it("marks a restricted model in the header so its access scope is visible at a glance", () => {
    // 受限可见决定"谁能通过 MCP 问到它"。只写在编辑对话框里的话，
    // 看模型的人不知道它是受限的 —— 故必须在标题行标出来
    const wrapper = mountWorkbench({
      tree: { ...tree, model: { ...model, visibility: "restricted" } },
    });

    expect(wrapper.text()).toContain("限制可见");
  });

  it("does not mark a public model as restricted", () => {
    expect(mountWorkbench().text()).not.toContain("限制可见");
  });

  it("hides the grant entry without the dedicated grant permission", () => {
    // ★ 授权是改访问控制，权限点与 canUpdate 分开：让"能编辑模型的人"顺带能开通
    // 受限模型的可见性，等于让访问控制的门由被它保护的人自己开
    expect(mountWorkbench({ canGrant: true }).text()).toContain("授权管理");
    expect(mountWorkbench({ canGrant: false, canUpdate: true }).text()).not.toContain("授权管理");
  });

  it("hides the fix button when the selected node has no fix target", () => {
    const wrapper = mountWorkbench({
      selected: {
        title: "JOIN 关系",
        nodeType: "join",
        items: [],
        light: "red",
        missing: ["缺少关联条件"],
      },
    });

    expect(wrapper.find(".check-card__fix").exists()).toBe(false);
  });
});
