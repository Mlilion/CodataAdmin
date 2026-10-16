# 参与贡献 🎉

欢迎任何形式的贡献 —— 代码、文档、Bug 反馈、功能建议。

## 快速链接

- 📖 **项目说明**: [README](./README.md)
- 🐛 **反馈 Bug**: 在当前仓库的 Issues 页面提交
- 💡 **功能建议**: 先提 Issue 讨论，再动手写代码
- 📝 **开发规范**: 遵循仓库现有代码风格和提交信息规范
- 💬 **社区交流**: 请使用仓库主页公布的社区渠道

## 开始之前

1. 先搜索仓库现有 Issues，避免重复提交
2. 较大改动建议先开 Issue 讨论方案
3. 遵循约定式提交信息规范

## 本地开发环境

```bash
# 后端
cd backend && uv sync && uv run main.py run --env=dev

# 前端
cd frontend/web && pnpm install && pnpm run dev

# 文档
cd frontend/docs && pnpm install && pnpm run dev
```

## Pull Request 流程

1. Fork 仓库，创建功能分支（`feature/xxx` 或 `bugfix/xxx`）
2. 完成修改，使用约定式提交信息
3. 运行检查：`ruff check`（后端）/ `pnpm lint`（前端）
4. 提交 PR 到项目默认分支
5. 等待审核 —— 我们会尽快回复

## 贡献者协议

提交贡献即表示你同意将贡献内容以项目的 [GNU GPL v3.0 协议](./LICENSE) 发布。

---

**每一点贡献都很重要 —— 从修复错别字到开发新功能。感谢！❤️**
