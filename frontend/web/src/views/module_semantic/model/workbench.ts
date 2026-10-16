import type { ModelTree, TreeEntityNode } from "@/api/module_semantic/model";
import { MarkerType, type Edge, type Node } from "@vue-flow/core";

export const ENTITY_TYPE_MAP: Record<string, string> = {
  fact: "事实",
  dimension: "维表",
  detail: "明细",
  summary: "汇总",
};

export const KNOWLEDGE_TYPE_MAP: Record<string, string> = {
  background: "背景",
  usage: "使用说明",
  caveat: "避坑",
  query_pattern: "查询范式",
  glossary: "术语",
};

export const FIELD_ROLE_MAP: Record<string, string> = {
  measure: "度量",
  dimension: "维度",
  time: "时间",
  id: "标识",
};

export const REL_MAP: Record<string, string> = {
  many_to_one: "N:1",
  one_to_many: "1:N",
  one_to_one: "1:1",
};

export interface WorkbenchStats {
  entities: number;
  fields: number;
  dimensions: number;
  indicators: number;
  knowledge: number;
  joins: number;
  blockers: number;
}

export function collectWorkbenchStats(tree: ModelTree): WorkbenchStats {
  return tree.entities.reduce<WorkbenchStats>(
    (stats, entity) => ({
      ...stats,
      entities: stats.entities + 1,
      fields: stats.fields + entity.fields.length,
      dimensions: stats.dimensions + entity.dimensions.length,
      indicators: stats.indicators + entity.indicators.length,
      knowledge: stats.knowledge + entity.knowledge.length,
    }),
    {
      entities: 0,
      fields: 0,
      dimensions: 0,
      indicators: 0,
      knowledge: tree.model_knowledge.length,
      joins: tree.joins.length,
      blockers: tree.publish_blockers.length,
    }
  );
}

export function calculateModelCompleteness(tree: ModelTree): number {
  const lights = [
    tree.model_completeness?.light,
    ...tree.entities.flatMap((entity) => [
      entity.completeness?.light,
      ...entity.fields.map((field) => field.completeness?.light),
      ...entity.dimensions.map((dimension) => dimension.completeness?.light),
      ...entity.indicators.map((indicator) => indicator.completeness?.light),
      ...entity.knowledge.map((knowledge) => knowledge.completeness?.light),
    ]),
    ...tree.model_knowledge.map((knowledge) => knowledge.completeness?.light),
    ...tree.joins.map((join) => join.completeness?.light),
  ].filter((light): light is "green" | "yellow" | "red" => Boolean(light));

  if (!lights.length) return 100;

  const weights = { green: 1, yellow: 0.65, red: 0 } as const;
  const score = lights.reduce((total, light) => total + weights[light], 0);
  return Math.round((score / lights.length) * 100);
}

export function resolveFilteredModelId<T extends { id?: number }>(
  models: T[],
  currentId?: number
): number | undefined {
  if (currentId && models.some((model) => model.id === currentId)) return currentId;
  return models.find((model) => model.id !== undefined)?.id;
}

export function canApplySelectionResponse<T>(
  requestId: number,
  latestRequestId: number,
  requestedSelection: T,
  currentSelection: T
): boolean {
  return requestId === latestRequestId && requestedSelection === currentSelection;
}

function estimateEntityHeight(entity: TreeEntityNode): number {
  const groupHeights = [
    Math.min(entity.fields.length, 4),
    Math.min(entity.dimensions.length, 2),
    Math.min(entity.indicators.length, 2),
    Math.min(entity.knowledge.length, 2),
  ]
    .filter((count) => count > 0)
    .reduce((height, count) => height + 35 + count * 24, 0);
  return 58 + groupHeights;
}

export function buildEntityGraph(tree: ModelTree): { nodes: Node[]; edges: Edge[] } {
  const ordered = [...tree.entities].sort((a, b) => a.sort - b.sort);
  const primary = ordered.find((entity) => entity.role === "primary") ?? ordered[0];
  const related = ordered.filter((entity) => entity.id !== primary?.id);

  const nodes: Node[] = [];
  if (primary) {
    nodes.push({
      id: `entity-${primary.id}`,
      type: "entity",
      position: { x: 420, y: 180 },
      data: { entity: primary },
    });
  }

  const columnY = { left: 80, right: 80 };
  related.forEach((entity, index) => {
    const side = index % 2 === 0 ? "left" : "right";
    nodes.push({
      id: `entity-${entity.id}`,
      type: "entity",
      position: {
        x: side === "left" ? 40 : 800,
        y: columnY[side],
      },
      data: { entity },
    });
    columnY[side] += estimateEntityHeight(entity) + 56;
  });

  const nodeIds = new Set(nodes.map((node) => node.id));
  const nodePositions = new Map(nodes.map((node) => [node.id, node.position]));
  const edges: Edge[] = tree.joins
    .filter(
      (join) =>
        nodeIds.has(`entity-${join.from_entity_id}`) && nodeIds.has(`entity-${join.to_entity_id}`)
    )
    .map((join) => {
      const source = `entity-${join.from_entity_id}`;
      const target = `entity-${join.to_entity_id}`;
      const sourceIsLeft =
        (nodePositions.get(source)?.x ?? 0) < (nodePositions.get(target)?.x ?? 0);
      return {
        id: `join-${join.id}`,
        source,
        target,
        sourceHandle: sourceIsLeft ? "source-right" : "source-left",
        targetHandle: sourceIsLeft ? "target-left" : "target-right",
        label: `${REL_MAP[join.relationship_type] ?? join.relationship_type} · ${join.join_type.toUpperCase()} JOIN`,
        type: "smoothstep",
        markerEnd: MarkerType.ArrowClosed,
        data: { join },
        style: { stroke: "#64748b", strokeWidth: 1.5 },
        labelStyle: { fill: "#475569", fontSize: 11, fontWeight: 600 },
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.94 },
        labelBgPadding: [6, 4] as [number, number],
        labelBgBorderRadius: 4,
      };
    });

  return { nodes, edges };
}
