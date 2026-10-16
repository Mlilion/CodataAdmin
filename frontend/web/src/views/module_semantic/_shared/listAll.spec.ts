import { expect, test, vi } from "vitest";
import { fetchAllPages, LIST_PAGE_SIZE } from "./listAll";

const page = (items: unknown[], total: number) => ({ data: { data: { items, total } } });

test("页大小恒为后端上限 100", async () => {
  const fn = vi.fn(async () => page([], 0));
  await fetchAllPages(fn);
  expect(fn).toHaveBeenCalledWith({ page_no: 1, page_size: LIST_PAGE_SIZE });
  expect(LIST_PAGE_SIZE).toBeLessThanOrEqual(100);
});

test("单页取满 total 时只请求一次", async () => {
  const fn = vi.fn(async () => page([1, 2, 3], 3));
  expect(await fetchAllPages(fn)).toEqual([1, 2, 3]);
  expect(fn).toHaveBeenCalledTimes(1);
});

test("跨页聚合按 page_no 递增直到取满", async () => {
  const fn = vi.fn(async ({ page_no }: { page_no: number }) =>
    page_no === 1 ? page(Array.from({ length: 100 }, (_, i) => i), 150) : page(Array.from({ length: 50 }, (_, i) => 100 + i), 150)
  );
  const out = await fetchAllPages(fn);
  expect(out).toHaveLength(150);
  expect(out[149]).toBe(149);
  expect(fn).toHaveBeenCalledTimes(2);
});

test("total 异常大于返回量时空页终止,不死循环", async () => {
  const fn = vi.fn(async ({ page_no }: { page_no: number }) => (page_no === 1 ? page([1], 999) : page([], 999)));
  expect(await fetchAllPages(fn)).toEqual([1]);
  expect(fn).toHaveBeenCalledTimes(2);
});

test("响应缺 data 时安全返回空", async () => {
  const fn = vi.fn(async () => ({ data: {} }));
  expect(await fetchAllPages(fn)).toEqual([]);
});
