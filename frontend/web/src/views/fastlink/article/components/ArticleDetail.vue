<!-- 文章详情（弹窗，无独立路由） -->
<template>
  <FaDialog
    v-model="visible"
    :title="dialogTitle"
    width="min(900px, 96vw)"
    :show-footer="false"
    class="article-detail-dialog"
  >
    <div v-loading="loading" class="article-detail-inner">
      <ElEmpty v-if="error" :description="error" />
      <div
        v-else-if="articleHtml"
        class="markdown-body article-detail-markdown"
        v-highlight
        v-html="articleHtml"
      />
    </div>
    <template #footer>
      <div class="flex flex-wrap justify-end gap-2">
        <ElButton v-if="articleId != null" @click="emitOpenCommentWall">
          <FaSvgIcon icon="ri:message-3-line" class="mr-1" />
          留言讨论
        </ElButton>
        <ElButton @click="visible = false">关闭</ElButton>
        <ElButton v-if="articleId != null" type="primary" @click="onEditClick"> 编辑 </ElButton>
      </div>
    </template>
  </FaDialog>
</template>

<script setup lang="ts">
import "./_markdown.scss";
import "./_highlight.scss";
import DOMPurify from "dompurify";
import { ArticleDetail } from "@/mock/temp/articleDetail";

defineOptions({ name: "ArticleDetail" });

const props = defineProps<{
  modelValue: boolean;
  articleId?: number | null;
}>();

interface Emits {
  "update:modelValue": [boolean];
  edit: [id: number];
  "open-comment-wall": [];
}

const emit = defineEmits<Emits>();

const emitOpenCommentWall = () => {
  emit("open-comment-wall");
};

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit("update:modelValue", v),
});

const articleTitle = ref("");
const articleHtml = shallowRef("");
const loading = ref(false);
const error = ref<string | null>(null);

const dialogTitle = computed(() => articleTitle.value || "文章详情");

const fetchDetail = async () => {
  const id = props.articleId;
  if (id == null) return;

  loading.value = true;
  error.value = null;
  articleTitle.value = "";
  articleHtml.value = "";

  try {
    // 使用 mock 数据
    await new Promise((resolve) => setTimeout(resolve, 300));

    const mockHtml = ArticleDetail[id];
    if (mockHtml) {
      const titleMatch = mockHtml.match(/<h2[^>]*>(.*?)<\/h2>/);
      articleTitle.value = titleMatch?.[1]?.replace(/<[^>]*>/g, "") || "文章详情";
      articleHtml.value = DOMPurify.sanitize(mockHtml);
    } else {
      error.value = "暂无该文章内容";
    }
  } catch (err) {
    error.value = "文章加载失败，请检查网络连接";
    console.error("获取文章详情失败:", err);
  } finally {
    loading.value = false;
  }
};

const onEditClick = () => {
  if (props.articleId != null) {
    emit("edit", props.articleId);
  }
};

watch(
  () => [props.modelValue, props.articleId] as const,
  ([open, id]) => {
    if (open && id != null) {
      nextTick(() => fetchDetail());
    }
  }
);
</script>

<style lang="scss" scoped>
.article-detail-inner {
  min-height: 120px;
  max-height: 70vh;
  overflow-y: auto;
}

.article-detail-markdown {
  margin-top: 8px;

  :deep(img) {
    width: 100%;
    border: 1px solid var(--fa-gray-200);
  }

  :deep(pre) {
    position: relative;

    &:hover {
      .copy-button {
        opacity: 1;
      }
    }

    &::before {
      position: absolute;
      top: 0;
      left: 50px;
      width: 1px;
      height: 100%;
      content: "";
      background: #0a0a0e;
    }
  }

  :deep(.code-wrapper) {
    overflow-x: auto;
  }

  :deep(.line-number) {
    position: sticky;
    left: 0;
    z-index: 2;
    box-sizing: border-box;
    display: inline-block;
    width: 50px;
    margin-right: 10px;
    font-size: 14px;
    color: #9e9e9e;
    text-align: center;
  }

  :deep(.copy-button) {
    position: absolute;
    top: 6px;
    right: 6px;
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    font-size: 20px;
    line-height: 40px;
    color: #999;
    text-align: center;
    cursor: pointer;
    background-color: #000;
    border: none;
    border-radius: 8px;
    opacity: 0;
    transition: all 0.2s;
  }
}
</style>
