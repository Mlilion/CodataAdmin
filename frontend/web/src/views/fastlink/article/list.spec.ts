import { describe, expect, it } from "vitest";

import { filterAndPaginateArticles, type ArticleListItem } from "./list";

const articles: ArticleListItem[] = [
  {
    id: 3,
    title: "Order revenue",
    create_time: "2024-05-01T00:00:00.000Z",
  },
  {
    id: 2,
    title: "Customer profile",
    create_time: "2023-05-01T00:00:00.000Z",
  },
  {
    id: 1,
    title: "Order detail",
    create_time: "2023-04-01T00:00:00.000Z",
  },
];

describe("article list filtering and pagination", () => {
  it("filters by title and year", () => {
    const result = filterAndPaginateArticles(articles, {
      search: "order",
      year: "2023",
      page: 1,
      pageSize: 40,
    });

    expect(result.total).toBe(1);
    expect(result.items.map((item) => item.id)).toEqual([1]);
  });

  it("returns the requested page and clamps an out-of-range page", () => {
    const result = filterAndPaginateArticles(articles, {
      search: "",
      year: "All",
      page: 9,
      pageSize: 2,
    });

    expect(result.page).toBe(2);
    expect(result.items.map((item) => item.id)).toEqual([1]);
  });
});
