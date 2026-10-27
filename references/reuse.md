# 与 Review Gate 的关系

本仓库对照的 Review Gate 提交是 `52083e4cca6439e422edb9acc73708c49166d9fe`：

https://github.com/sanjinlingyan/review-gate/commit/52083e4cca6439e422edb9acc73708c49166d9fe

该提交的许可证是 MIT。本仓库没有把它当作运行依赖，也不读取开发机上的 Review Gate 目录。

## 沿用的部分

- 正式产出仍是 `review.html` 和 `repair.md`。
- 报告按“文件 → 原文章节 → 提炼要求”阅读，保留全部 / 仅未通过切换、完成率和通过率口径。
- 修复单的责任、依据、位置、动作和复验条件与现行修复单同一顺序。
- 进度和结论分开；证据要绑定到文件哈希。
- 路径越界和压缩包链接的防护按同一安全目标重新实现。

报告样式在 `agent_review/render.py`，来源于上述提交中的章节报告。

## 本仓库单独做的部分

Review Gate 在 `run` 时要求已经准备好 `context.v3`，并同时做交付结构、任务摘要和工程诊断。那些诊断需要 Agent 再逐条解释。

本仓库的 `run` 只冻结依据和交付。义务清单由 Agent 在这次运行里从原文写成 `catalog.json`，判断写成 `results.json`。脚本再检查段落覆盖、逻辑收紧、证据绑定和评分器 `unknown` 的原因，然后生成同一阅读方式的两份报告。

因此这条路径可以单独使用。它不把工程诊断的 fail 或 unknown 收成验收结论，也不把旧的 Gate 清单当成原文已经提炼正确。
