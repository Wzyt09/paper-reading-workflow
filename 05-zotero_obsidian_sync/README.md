# Zotero / Obsidian 同步工具

这是当前工作区使用的 Zotero + AI 总结 + Obsidian 同步目录。

## 推荐入口

优先直接双击：

- `paper_sync_gui.cmd`

兼容旧入口：

- `run_sync_gui.cmd`

这两个入口现在都会启动同一个新版 GUI，不再区分新旧界面。

## 主要能力

- 监视 `01-paper` 中的新 PDF
- 将选中的 PDF 自动加入 Zotero 集合 `01-paper-sync`
- 对 Zotero 条目生成或复用 AI 总结
- 将 `01-paper-sync` 的全部条目统一导出到 Obsidian
- 对未总结条目也生成基础元数据页
- 将本地总结 Markdown 以 `linked_file` 方式挂回 Zotero

## 重要文件

- `GUI_WORKFLOW.md`
  当前图形化工作流的详细中文说明
- `config.json`
  当前同步配置
- `paper_sync_gui.py`
  新版 GUI 主程序
- `sync_pipeline.py`
  单篇总结、打包、回写 Zotero 的主流程
- `sync_existing_packages.py`
  将整个 `01-paper-sync` 重新同步到 Obsidian

## 常用操作

- 打开 GUI：双击 `paper_sync_gui.cmd`
- 打开 Obsidian 全量同步：双击 `sync_collection_to_obsidian.cmd`
- 修改默认总结规范：编辑 `..\02-paper_summary_specs\default.md`

## 说明

- Zotero 桌面端需要保持打开。
- 如果没有可用的 OpenAI API / Codex CLI，就不能自动生成新的 AI 总结，但已有总结仍可继续同步到 Zotero 和 Obsidian。
- 当前推荐以 `GUI_WORKFLOW.md` 作为主要使用文档，本 README 只保留最小入口说明。
