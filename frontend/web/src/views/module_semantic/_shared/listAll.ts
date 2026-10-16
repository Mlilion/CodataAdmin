/**
 * 选择器组件的「取全量」辅助:后端 PageQuery 校验 page_size ≤ 100,
 * 超限请求直接 422(下拉只剩空列表)。这里按 100/页循环拉取直到取满 total。
 */

/** 后端允许的最大页大小,超过会被 422 拒绝 */
export const LIST_PAGE_SIZE = 100;

/** 聚合硬上限,防止异常 total 导致死循环 */
const MAX_TOTAL = 1000;

/** 只声明用到的字段,兼容后端 PageResult(避免把整个形状钉死) */
type PageFetcher<T> = (q: {
  page_no: number;
  page_size: number;
}) => Promise<{ data: { data?: { items?: T[] | null; total?: number } | null } }>;

export async function fetchAllPages<T>(fetchPage: PageFetcher<T>): Promise<T[]> {
  const out: T[] = [];
  let pageNo = 1;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const r = await fetchPage({ page_no: pageNo, page_size: LIST_PAGE_SIZE });
    const items = r.data.data?.items ?? [];
    out.push(...items);
    const total = r.data.data?.total ?? 0;
    if (!items.length || out.length >= total || out.length >= MAX_TOTAL) break;
    pageNo++;
  }
  return out;
}
