# paper-reading-workflow

一个面向 Zotero + Obsidian 的本地论文阅读工作流。它可以从 PDF 和 Zotero 元数据中提取信息，调用大模型生成单篇论文总结或多篇论文对比总结，并把结果整理成便于长期维护的 Markdown 文档库。

![工作流总览](docs/assets/workflow-overview.svg)

## 功能概览

- **单篇论文总结**：提取 PDF 文本、元数据、图片清单和参考信息，按默认规范生成结构化 Markdown。
- **多篇论文对比**：把多篇论文放入同一个对比项目文件夹，保留原文、单篇总结、对比材料和最终对比报告。
- **自定义总结要求**：生成总结前可输入本次关心的问题、阅读目标、输出侧重点，再与默认规范合并后提交给大模型。
- **Zotero 联动**：支持读取 Zotero 本地库或 Better BibTeX 导出数据，并可把总结 Markdown 附回 Zotero 条目。
- **Obsidian 联动**：输出为普通 Markdown，保留双链、标签、附件目录，适合直接作为 Obsidian vault 使用。
- **多模型后端**：支持 OpenAI、OpenAI-compatible API、Codex CLI，可配置 DeepSeek、通义千问、Kimi、GLM 等兼容服务。
- **本地优先**：默认不上传 PDF 原文到 GitHub；配置文件和个人文献库会被 `.gitignore` 排除。

## 目录结构

```text
paper-reading-workflow/
├─ 02-paper-library/                 # 本地 PDF 和文献材料，默认不上传 GitHub
├─ 03-tools/
│  └─ pdf_tools/
│     ├─ extract_pdf.py              # PDF 文本、图片、公式等提取
│     └─ .venv/                      # 本地虚拟环境，默认不上传
├─ 04-summary-rules/
│  ├─ default.md                     # 单篇论文默认总结规范
│  └─ comparison_default.md          # 多篇论文对比总结规范
├─ 05-zotero_obsidian_sync/
│  ├─ sync_pipeline.py               # 命令行主入口
│  ├─ paper_sync_gui.py              # 图形界面入口
│  ├─ package_summary.py             # 总结打包和回链
│  ├─ config.example.json            # 配置模板
│  └─ config.json                    # 你的本地配置，默认不上传
├─ docs/
│  ├─ assets/                        # README 自绘示意图
│  └─ examples/                      # 脱敏示例文档
└─ setup_windows.ps1                 # Windows 初始化脚本
```

![配置结构](docs/assets/config-map.svg)

## 配置需求

### 基础环境

推荐环境：

- Windows 10/11
- PowerShell 5.1 或 PowerShell 7
- Python 3.11 或更新版本
- Git
- Zotero 7
- Obsidian 1.5 或更新版本

初始化：

```powershell
cd D:\github\paper-reading-workflow
.\setup_windows.ps1
```

脚本会创建 Python 虚拟环境并安装 `requirements.txt` 中的依赖。完成后，复制配置模板：

```powershell
Copy-Item .\05-zotero_obsidian_sync\config.example.json .\05-zotero_obsidian_sync\config.json
```

之后只编辑 `config.json`。不要把真实 API key 写入仓库，建议使用环境变量。

### 大模型配置

本项目支持三类后端：

- `openai`：OpenAI 官方 API。
- `openai_compatible`：兼容 OpenAI Chat Completions 或 Responses 风格的服务，适合 DeepSeek、通义千问、Kimi、GLM 等。
- `codex`：调用本机 Codex CLI。

DeepSeek 示例：

```json
{
  "summary_backend": "openai_compatible",
  "llm": {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY",
    "model": "deepseek-chat",
    "temperature": 0.2
  }
}
```

通义千问 DashScope OpenAI-compatible 示例：

```json
{
  "summary_backend": "openai_compatible",
  "llm": {
    "provider": "dashscope",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key_env": "DASHSCOPE_API_KEY",
    "model": "qwen-plus",
    "temperature": 0.2
  }
}
```

Kimi 示例：

```json
{
  "summary_backend": "openai_compatible",
  "llm": {
    "provider": "moonshot",
    "base_url": "https://api.moonshot.cn/v1",
    "api_key_env": "MOONSHOT_API_KEY",
    "model": "moonshot-v1-32k",
    "temperature": 0.2
  }
}
```

设置环境变量：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "你的 key", "User")
```

重新打开 PowerShell 后生效。

## Zotero 配置指引

![Zotero 与 Obsidian 联动](docs/assets/output-layout.svg)

### 方案 A：读取 Zotero 本地数据库

适合只在一台电脑上使用。配置项通常包括：

```json
{
  "zotero": {
    "db_path": "C:/Users/你的用户名/Zotero/zotero.sqlite",
    "storage_dir": "C:/Users/你的用户名/Zotero/storage",
    "collection_name": "待读文章"
  }
}
```

建议：

- 运行批处理前关闭 Zotero，或确保 Zotero 没有正在写入数据库。
- 路径使用 `/` 或转义后的 `\\`，避免 JSON 路径解析错误。
- 如果你的 Zotero library 放在 NAS 或同步盘中，优先使用稳定的本地缓存路径。

### 方案 B：使用 Better BibTeX 导出

适合跨电脑、跨系统使用，也更适合作为公开项目的推荐方式。

1. 在 Zotero 安装 Better BibTeX 插件。
2. 右键目标 collection，选择导出。
3. 格式选择 Better BibTeX JSON。
4. 勾选 keep updated。
5. 把导出的 JSON 路径写入配置。

示例：

```json
{
  "zotero": {
    "bbt_export_path": "D:/paper-reading-data/zotero_export.json"
  }
}
```

如果项目提供了 `setup-bbt` 子命令，也可以用它辅助创建或检查导出配置。

### 写回 Zotero

如果希望把 Markdown 总结附回 Zotero 条目，需要配置 Zotero Web API：

```json
{
  "zotero_writeback": {
    "enabled": true,
    "user_id": "你的 Zotero user id",
    "library_type": "user",
    "api_key_env": "ZOTERO_API_KEY",
    "update_summary_note": true,
    "attach_summary_markdown": true,
    "summary_attachment_mode": "link"
  }
}
```

注意：

- Zotero API key 只放在环境变量中。
- `summary_attachment_mode` 推荐使用 `link`，这样 Zotero 里保存的是本地 Markdown 链接，不会复制出多个版本。
- 如果使用 group library，`library_type` 需要改为 `group`，并填写对应 group id。

## Obsidian 配置指引

Obsidian 不需要插件即可使用本项目输出。推荐配置：

```json
{
  "obsidian": {
    "vault_dir": "D:/Obsidian/PaperVault",
    "papers_subdir": "papers",
    "tags_subdir": "tags",
    "tag_prefix": "paper"
  }
}
```

输出示例：

```text
D:/Obsidian/PaperVault/
├─ papers/
│  ├─ 2024-author-title/
│  │  ├─ summary.md
│  │  ├─ source.pdf
│  │  ├─ manifest.json
│  │  └─ _attachments/
│  └─ comparisons/
│     └─ 2026-04-26-rydberg-crosstalk/
│        ├─ comparison.md
│        ├─ manifest.json
│        ├─ sources/
│        └─ per-paper-summaries/
└─ tags/
```

Obsidian 中可以直接使用：

- `[[summary]]` 跳转到单篇总结。
- `[[comparison]]` 跳转到对比总结。
- `#paper/quantum-computing` 这类标签做主题聚合。
- Dataview 插件可选，用来做阅读进度、主题、年份、模型等索引。

## 使用方法

### 启动图形界面

```powershell
cd D:\github\paper-reading-workflow
.\03-tools\pdf_tools\.venv\Scripts\python.exe .\05-zotero_obsidian_sync\paper_sync_gui.py
```

图形界面的推荐流程：

1. 选择 Zotero collection 或本地 PDF 文件。
2. 选择单篇总结或多篇对比。
3. 在弹出的对话框里输入本次阅读目标、关键问题和格式偏好。
4. 运行总结。
5. 检查输出的 Markdown、附件和 Zotero/Obsidian 链接。

![图形界面流程](docs/assets/gui-flow.svg)

### 单篇总结

```powershell
.\03-tools\pdf_tools\.venv\Scripts\python.exe .\05-zotero_obsidian_sync\sync_pipeline.py summarize `
  --pdf "D:/paper-reading-data/pdfs/example.pdf" `
  --summary-backend openai_compatible
```

默认规范来自 `04-summary-rules/default.md`。运行时输入的额外要求会追加到默认规范后，例如：

```text
请重点解释实验设计、主要假设、图 2 和图 4 的含义，并列出我后续复现实验需要检查的参数。
```

### 多篇论文对比

```powershell
.\03-tools\pdf_tools\.venv\Scripts\python.exe .\05-zotero_obsidian_sync\sync_pipeline.py compare `
  --pdf "D:/papers/a.pdf" `
  --pdf "D:/papers/b.pdf" `
  --pdf "D:/papers/c.pdf" `
  --topic "Rydberg quantum computing crosstalk"
```

多篇对比默认规范来自 `04-summary-rules/comparison_default.md`。推荐对比模式包括：

- **问题定义**：每篇文章分别解决什么问题，问题之间是什么关系。
- **方法路线**：理论、实验、模拟、系统实现分别采用什么技术路线。
- **关键结论**：每篇文章最核心的结论、适用条件和证据强度。
- **图表证据**：尽量列出关键图片、表格、公式和它们支撑的论点。
- **差异矩阵**：研究对象、模型假设、实验设置、评价指标、局限性逐项对比。
- **综合判断**：哪些结论相互支持，哪些结论存在冲突，下一步应该读什么或验证什么。

### 打包已有总结

```powershell
.\03-tools\pdf_tools\.venv\Scripts\python.exe .\05-zotero_obsidian_sync\package_summary.py `
  --summary "D:/paper-reading-data/summaries/example.md" `
  --pdf "D:/paper-reading-data/pdfs/example.pdf"
```

打包会生成 `manifest.json`，记录原文、总结、附件、模型、时间和回链信息，便于后续迁移。

## 示例

公开仓库中的示例都是脱敏改写，不包含你的私有 PDF、论文原图或完整笔记：

- [单篇论文总结示例](docs/examples/single-paper-summary-demo.md)
- [多篇论文对比示例](docs/examples/multi-paper-comparison-demo.md)
- [DeepSeek 配置示例](docs/examples/config.deepseek.example.json)

单篇总结片段：

```text
一句话结论：
本文展示了一种在中性原子量子计算平台中降低串扰影响的实验策略，
核心价值在于把误差来源拆分为可测量、可校准、可比较的几个部分。

关键图片：
- 图 1：实验系统与原子阵列结构。
- 图 2：串扰随距离和脉冲参数变化的测量结果。
- 图 4：校准策略前后的保真度对比。
```

多篇对比片段：

```text
综合判断：
三篇文章都关注 Rydberg 阵列中的门操作误差，但侧重点不同：
A 更像是误差表征框架，B 给出控制脉冲优化方案，C 讨论规模化系统中的工程约束。
如果目的是设计实验，优先读 A 和 B；如果目的是评估架构可扩展性，C 更关键。
```

## 生成结果示意

```text
comparison-project/
├─ comparison.md                     # 最终对比总结
├─ user_request.md                   # 本次弹窗输入的特定要求
├─ comparison_prompt.md              # 默认规范 + 用户要求
├─ manifest.json
├─ sources/
│  ├─ paper-a.pdf
│  ├─ paper-b.pdf
│  └─ paper-c.pdf
├─ extracted/
│  ├─ paper-a/
│  ├─ paper-b/
│  └─ paper-c/
└─ per-paper-summaries/
   ├─ paper-a.summary.md
   ├─ paper-b.summary.md
   └─ paper-c.summary.md
```

每个单篇总结会链接到所属对比项目：

```markdown
相关对比：[[2026-04-26-rydberg-crosstalk/comparison]]
```

对比总结也会反向列出每篇论文：

```markdown
- [[paper-a.summary]]
- [[paper-b.summary]]
- [[paper-c.summary]]
```

## 隐私与发布说明

仓库默认不会上传以下内容：

- PDF 原文
- Zotero 数据库
- Obsidian 私有 vault
- `config.json`
- API key
- 生成的大批量总结缓存
- Python 虚拟环境

公开发布时建议只上传代码、模板、脱敏示例和自绘说明图。论文原图通常受版权限制，不建议直接放进公开 README；本仓库的 README 图片均为自绘 SVG，用来说明流程和目录结构。

## 常见问题

### 找不到 Zotero 条目

先确认 `collection_name` 是否和 Zotero 中完全一致。如果使用 Better BibTeX 导出，检查 JSON 是否已经自动更新。

### Obsidian 里链接打不开

检查 `vault_dir` 是否指向真实 vault 根目录，并确认 `papers_subdir` 没有多写一层目录。

### 模型调用失败

检查三件事：

- 环境变量是否生效：`Get-ChildItem Env:DEEPSEEK_API_KEY`
- `base_url` 是否包含正确的 `/v1` 或兼容路径。
- 模型名称是否是服务商当前支持的模型。

### 总结太长或不够聚焦

在生成前的弹窗中写清楚阅读目标，例如：

```text
我只关心方法和实验局限，不需要逐段翻译。请重点回答：
1. 这篇文章的核心假设是什么？
2. 关键图表分别支撑了什么结论？
3. 如果我要复现实验，最需要注意哪些参数？
```

### 是否已经是 Zotero 插件

当前版本是本地 Python 工具，不是原生 Zotero 插件。后续可以把核心能力拆成三层：

- Zotero 插件负责选择条目、收集 PDF、展示状态。
- 本地 Python 服务负责 PDF 提取、模型调用、文档打包。
- Obsidian vault 继续作为最终知识库。

这种方式可以保留现有本地功能，同时逐步增加插件体验。

## 许可证

如果你要公开发布，建议在正式版本中补充明确的开源许可证，例如 MIT、Apache-2.0 或 GPL-3.0。
