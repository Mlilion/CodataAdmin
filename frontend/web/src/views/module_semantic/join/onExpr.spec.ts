import { expect, test } from "vitest";
import { buildOnExpr, parseOnExpr } from "./onExpr";

test("单条件生成与往返解析", () => {
  const expr = buildOnExpr([{ left: "pay_amount", right: "id" }], "order_revenue", "dim_user");
  expect(expr).toBe("order_revenue.pay_amount = dim_user.id");
  expect(parseOnExpr(expr, "order_revenue", "dim_user")).toEqual([
    { left: "pay_amount", right: "id" },
  ]);
});

test("多条件以顶层 AND 连接并可回显", () => {
  const rows = [
    { left: "order_date", right: "date_key" },
    { left: "region_id", right: "id" },
  ];
  const expr = buildOnExpr(rows, "fact_order", "dim_region");
  expect(expr).toBe(
    "fact_order.order_date = dim_region.date_key AND fact_order.region_id = dim_region.id"
  );
  expect(parseOnExpr(expr, "fact_order", "dim_region")).toEqual(rows);
});

test("生成时忽略未填完整的行", () => {
  expect(buildOnExpr([{ left: "a", right: "" }], "x", "y")).toBe("");
});

test("前缀方向与登记相反的存量表达式可回显(逐行交换)", () => {
  expect(parseOnExpr("dim_user.id = order_revenue.user_id", "order_revenue", "dim_user")).toEqual([
    { left: "user_id", right: "id" },
  ]);
});

test("前缀大小写不一致仍可回显(后端按规范化比对)", () => {
  expect(parseOnExpr("Order_Revenue.User_ID = DIM_USER.id", "order_revenue", "dim_user")).toEqual([
    { left: "User_ID", right: "id" },
  ]);
});

test("不可安全还原的表达式一律返回 null 交高级模式", () => {
  const cases = [
    "CAST(order_revenue.order_date AS CHAR) = dim_date.date", // 含函数
    "order_revenue.user_id = dim_user.user_id -- AND 1=1", // 注释
    "order_revenue.a = dim_user.b AND EXISTS (SELECT 1 FROM evil)", // 子查询
    "order_revenue.a = dim_user.b OR 1=1", // OR
    "user_id = users.id", // 裸列(无实体前缀)
    "order_revenue.a = totally_other.b", // 第三张表
    "db.order_revenue.a = dim_user.b", // 库限定
    "1=1",
    "   ",
    "",
  ];
  for (const c of cases) {
    expect(parseOnExpr(c, "order_revenue", "dim_user"), c).toBeNull();
  }
});
