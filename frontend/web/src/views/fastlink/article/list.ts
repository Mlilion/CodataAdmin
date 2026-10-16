export interface ArticleListItem {
  id: number;
  title: string;
  create_time: string;
}

export interface ArticleListQuery {
  search: string;
  year: string;
  page: number;
  pageSize: number;
}

export interface ArticleListPage<T> {
  items: T[];
  total: number;
  page: number;
}

export function filterAndPaginateArticles<T extends ArticleListItem>(
  articles: readonly T[],
  query: ArticleListQuery
): ArticleListPage<T> {
  const search = query.search.trim().toLocaleLowerCase();
  const filtered = articles.filter((article) => {
    const matchesSearch =
      !search || article.title.toLocaleLowerCase().includes(search);
    const matchesYear =
      query.year === "All" || article.create_time.slice(0, 4) === query.year;
    return matchesSearch && matchesYear;
  });

  const total = filtered.length;
  const pageCount = Math.max(1, Math.ceil(total / query.pageSize));
  const page = Math.min(Math.max(1, query.page), pageCount);
  const start = (page - 1) * query.pageSize;

  return {
    items: filtered.slice(start, start + query.pageSize),
    total,
    page,
  };
}
