// 语义中心跨页跳转 composable
// 各语义节点(实体/维度/指标)互相跳转,统一通过 router.push 带 focus query,
// 目标页在 onMounted 读取 route.query.focus 做高亮/筛选。
import { useRouter } from "vue-router";

export function useNavigateToNode() {
  const router = useRouter();
  const go = (path: string, id: number) => router.push({ path, query: { focus: id } });
  return {
    goEntity: (id: number) => go("/semantic/entity", id),
    goDimension: (id: number) => go("/semantic/dimension", id),
    goIndicator: (id: number) => go("/semantic/indicator", id),
  };
}
