import { expect, test, vi } from "vitest";

const push = vi.fn();
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));

import { useNavigateToNode } from "./useNavigateToNode";

test("goEntity push 到实体页并带 focus", () => {
  const { goEntity } = useNavigateToNode();
  goEntity(7);
  expect(push).toHaveBeenCalledWith({ path: "/semantic/entity", query: { focus: 7 } });
});

test("goDimension push 到维度页并带 focus", () => {
  const { goDimension } = useNavigateToNode();
  goDimension(3);
  expect(push).toHaveBeenCalledWith({ path: "/semantic/dimension", query: { focus: 3 } });
});

test("goIndicator push 到指标页并带 focus", () => {
  const { goIndicator } = useNavigateToNode();
  goIndicator(9);
  expect(push).toHaveBeenCalledWith({ path: "/semantic/indicator", query: { focus: 9 } });
});
