import { describe, expect, it } from "vitest";
import type { ModelTree } from "@/api/module_semantic/model";
import {
  buildEntityGraph,
  canApplySelectionResponse,
  calculateModelCompleteness,
  collectWorkbenchStats,
  resolveFilteredModelId,
} from "./workbench";

function makeTree(): ModelTree {
  return {
    model: {
      id: 10,
      code: "trade_analysis",
      name: "交易分析",
      description: "交易经营分析",
      version: 3,
      status: 2,
      visibility: "public",
    },
    domain_name: "交易域",
    model_completeness: { light: "green", missing: [] },
    publishable: false,
    publish_blockers: ["维度「地区」缺少主绑定"],
    model_knowledge: [],
    entities: [
      {
        member_id: 101,
        role: "primary",
        sort: 0,
        id: 1,
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
            completeness: { light: "green", missing: [] },
          },
          {
            id: 12,
            code: "pay_amount",
            name: "实付金额",
            completeness: { light: "yellow", missing: ["缺少单位"] },
          },
        ],
        dimensions: [
          {
            id: 21,
            code: "region",
            name: "地区",
            status: 1,
            completeness: { light: "red", missing: ["缺少主绑定"] },
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
        knowledge: [
          {
            id: 41,
            key: "order-caveat",
            summary: "订单查询说明",
            knowledge_type: "caveat",
            usage_mode: "always",
            ref_type: "entity",
            status: 2,
            completeness: { light: "yellow", missing: ["缺少正文"] },
          },
        ],
      },
      {
        member_id: 102,
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
    joins: [
      {
        id: 51,
        from_entity_id: 1,
        to_entity_id: 2,
        from_entity_code: "order",
        to_entity_code: "user",
        on_expr: "order.user_id = user.user_id",
        relationship_type: "many_to_one",
        join_type: "left",
        status: 2,
        completeness: { light: "green", missing: [] },
      },
    ],
  };
}

describe("semantic model workbench summaries", () => {
  it("collects exact semantic asset totals", () => {
    expect(collectWorkbenchStats(makeTree())).toEqual({
      entities: 2,
      fields: 2,
      dimensions: 1,
      indicators: 1,
      knowledge: 1,
      joins: 1,
      blockers: 1,
    });
  });

  it("calculates a weighted completeness percentage from node lights", () => {
    expect(calculateModelCompleteness(makeTree())).toBe(77);
  });
});

describe("semantic model relationship graph", () => {
  it("centers the primary entity and distributes related entities in side columns", () => {
    const tree = makeTree();
    tree.entities.push({
      ...tree.entities[1]!,
      member_id: 103,
      sort: 2,
      id: 3,
      code: "product",
      name: "商品",
    });

    const graph = buildEntityGraph(tree);

    expect(graph.nodes.map((node) => ({ id: node.id, position: node.position }))).toEqual([
      { id: "entity-1", position: { x: 420, y: 180 } },
      { id: "entity-2", position: { x: 40, y: 80 } },
      { id: "entity-3", position: { x: 800, y: 80 } },
    ]);
  });

  it("creates stable JOIN edges with readable relationship labels", () => {
    const graph = buildEntityGraph(makeTree());

    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0]).toMatchObject({
      id: "join-51",
      source: "entity-1",
      target: "entity-2",
      label: "N:1 · LEFT JOIN",
      type: "smoothstep",
      sourceHandle: "source-left",
      targetHandle: "target-right",
    });
  });

  it("spaces dense cards according to their estimated content height", () => {
    const tree = makeTree();
    const richRelated = {
      ...tree.entities[0]!,
      role: "related",
      fields: Array.from({ length: 4 }, (_, index) => ({
        id: 100 + index,
        code: `field_${index}`,
        name: `字段${index}`,
      })),
      dimensions: Array.from({ length: 2 }, (_, index) => ({
        id: 200 + index,
        code: `dimension_${index}`,
        name: `维度${index}`,
        status: 2,
      })),
      indicators: Array.from({ length: 2 }, (_, index) => ({
        id: 300 + index,
        code: `indicator_${index}`,
        name: `指标${index}`,
        indicator_type: "atomic",
        status: 2,
      })),
      knowledge: Array.from({ length: 2 }, (_, index) => ({
        id: 400 + index,
        key: `knowledge_${index}`,
        summary: `知识${index}`,
        knowledge_type: "usage",
        usage_mode: "always",
        ref_type: "entity",
        status: 2,
      })),
    };
    tree.entities = [
      tree.entities[0]!,
      { ...richRelated, member_id: 102, id: 2, code: "user", name: "用户", sort: 1 },
      { ...richRelated, member_id: 103, id: 3, code: "product", name: "商品", sort: 2 },
      { ...richRelated, member_id: 104, id: 4, code: "store", name: "门店", sort: 3 },
    ];

    const graph = buildEntityGraph(tree);
    const firstLeft = graph.nodes.find((node) => node.id === "entity-2")!;
    const secondLeft = graph.nodes.find((node) => node.id === "entity-4")!;

    expect(secondLeft.position.y - firstLeft.position.y).toBeGreaterThanOrEqual(440);
  });
});

describe("filtered model selection", () => {
  it("keeps the current model when it remains in the filtered list", () => {
    expect(
      resolveFilteredModelId(
        makeTree().entities.map((entity) => ({ id: entity.id })),
        2
      )
    ).toBe(2);
  });

  it("falls back to the first model or clears selection when the list is empty", () => {
    expect(resolveFilteredModelId([{ id: 7 }, { id: 8 }], 2)).toBe(7);
    expect(resolveFilteredModelId([], 2)).toBeUndefined();
  });
});

describe("selection request sequencing", () => {
  it("accepts only the latest response for the still-selected filter", () => {
    expect(canApplySelectionResponse(3, 3, 20, 20)).toBe(true);
    expect(canApplySelectionResponse(2, 3, 20, 20)).toBe(false);
    expect(canApplySelectionResponse(3, 3, 20, 21)).toBe(false);
    expect(canApplySelectionResponse(3, 3, undefined, undefined)).toBe(true);
  });
});
