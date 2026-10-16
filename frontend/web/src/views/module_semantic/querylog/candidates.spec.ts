import { describe, expect, it } from "vitest";

import { approveMessage, callerParam, candidateKey, removeCandidate, resolveNodeType } from "./candidates";

describe("candidateKey", () => {
  it("同一别名挂不同资产时 key 必须不同", () => {
    // ★ 真实场景：「人均收入」既召回 arpu 也召回 arppu，是两条独立待审记录。
    // 只用 alias 作 key 会让采纳一个就抹掉另一个。
    const a = { alias: "人均收入", node_type: "indicator", node_code: "arpu" };
    const b = { alias: "人均收入", node_type: "indicator", node_code: "arppu" };

    expect(candidateKey(a)).not.toBe(candidateKey(b));
  });

  it("node_type 为 null 时不炸且可稳定比较", () => {
    const row = { alias: "会员营收", node_type: null, node_code: "sub_revenue" };

    expect(candidateKey(row)).toBe(candidateKey({ ...row }));
  });
});

describe("removeCandidate", () => {
  const arpu = { alias: "人均收入", node_type: "indicator", node_code: "arpu" };
  const arppu = { alias: "人均收入", node_type: "indicator", node_code: "arppu" };
  const sub = { alias: "会员营收", node_type: "indicator", node_code: "sub_revenue" };
  const list = [arpu, arppu, sub];

  it("只移除被采纳的那一条", () => {
    const left = removeCandidate(list, arpu);

    expect(left).toHaveLength(2);
    expect(left.map(x => x.node_code)).toEqual(["arppu", "sub_revenue"]);
  });

  it("★ 不误删同名但归属不同资产的候选", () => {
    const left = removeCandidate(list, arppu);

    expect(left.some(x => x.node_code === "arpu")).toBe(true);
  });

  it("目标不在列表时原样返回", () => {
    const left = removeCandidate(list, {
      alias: "不存在", node_type: "indicator", node_code: "x",
    });

    expect(left).toHaveLength(3);
  });
});

describe("resolveNodeType", () => {
  it("dimension 原样传", () => {
    expect(resolveNodeType("dimension")).toBe("dimension");
  });

  it("★ null 回退 indicator 而非提交 null", () => {
    // 提交 null 会让后端报「不支持的资产类型」，用户看到无从下手的失败
    expect(resolveNodeType(null)).toBe("indicator");
  });

  it("未知类型也收敛到 indicator（后端只认两种）", () => {
    expect(resolveNodeType("knowledge")).toBe("indicator");
  });
});

describe("approveMessage", () => {
  it("★ 幂等重复采纳要说清，别让它看起来像没生效", () => {
    expect(approveMessage(true)).toContain("已存在");
    expect(approveMessage(false)).toContain("已生效");
  });
});

describe("callerParam", () => {
  it("空选择转成 undefined —— 不让 caller= 这种空参数进 URL 与操作日志", () => {
    expect(callerParam("")).toBeUndefined();
    expect(callerParam(null)).toBeUndefined();
    expect(callerParam(undefined)).toBeUndefined();
    expect(callerParam("   ")).toBeUndefined();
  });

  it("选了具体调用方就原样传（去掉首尾空白）", () => {
    expect(callerParam("codata")).toBe("codata");
    expect(callerParam(" ops-bot ")).toBe("ops-bot");
  });
});
