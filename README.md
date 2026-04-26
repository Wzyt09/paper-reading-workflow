# Paper Reading Workflow

Local workflow for extracting PDFs, generating Chinese paper summaries, packaging source materials, syncing summaries to Obsidian, and linking results back to Zotero.

## Features

- Single-paper Chinese summaries from PDF text, figures, formulas, and metadata.
- Multi-paper comparison summaries under `06-paper_comparisons/`.
- GUI workflow for selecting Zotero items and entering per-run summary requirements.
- Packaging of Markdown, source PDFs, extracted materials, images, and manifest files.
- Optional Obsidian vault export and Zotero note/attachment write-back.
- Model backends:
  - OpenAI Responses API.
  - OpenAI-compatible Chat Completions APIs such as DeepSeek, Qwen/DashScope-compatible gateways, Moonshot/Kimi-compatible gateways, and GLM-compatible gateways.
  - Codex CLI.

## Repository Layout

```text
02-paper_summary_specs/        Summary and comparison specs
03-tools/pdf_tools/            PDF extraction tools
03-tools/paper_workflow/       Summary packaging tools
05-zotero_obsidian_sync/       Zotero/Obsidian sync pipeline and GUI
```

Private paper data, generated summaries, PDF files, Obsidian vaults, Zotero state, caches, and local config files are intentionally ignored by Git.

## Quick Start

1. Install Python 3.11+.
2. Copy the example config:

```powershell
Copy-Item .\05-zotero_obsidian_sync\config.example.json .\05-zotero_obsidian_sync\config.json
```

3. Put PDFs under `01-paper/`.
4. Configure one model backend.

OpenAI:

```powershell
$env:OPENAI_API_KEY="sk-..."
```

OpenAI-compatible provider, for example DeepSeek:

```powershell
$env:OPENAI_COMPATIBLE_API_KEY="..."
```

Then set `openai_compatible.model` and `openai_compatible.endpoint` in `config.json`.

5. Run the GUI:

```powershell
.\05-zotero_obsidian_sync\paper_sync_gui.cmd
```

Or run the pipeline directly:

```powershell
python .\05-zotero_obsidian_sync\sync_pipeline.py once --pdf .\01-paper\example.pdf
```

Multi-paper comparison:

```powershell
python .\05-zotero_obsidian_sync\sync_pipeline.py compare `
  --summary-backend openai_compatible `
  --summary-model deepseek-chat `
  --summary-user-request "重点比较核心图、实验指标和后续可扩展性" `
  --pdf .\01-paper\a.pdf .\01-paper\b.pdf
```

## Provider Examples

DeepSeek:

```json
"openai_compatible": {
  "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
  "model": "deepseek-chat",
  "endpoint": "https://api.deepseek.com/v1/chat/completions"
}
```

Qwen/DashScope OpenAI-compatible mode:

```json
"openai_compatible": {
  "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
  "model": "qwen-plus",
  "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
}
```

Moonshot/Kimi:

```json
"openai_compatible": {
  "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
  "model": "moonshot-v1-32k",
  "endpoint": "https://api.moonshot.cn/v1/chat/completions"
}
```

Zhipu/GLM compatibility depends on the account endpoint you use; set the endpoint to the provider's OpenAI-compatible chat-completions URL.

## Zotero Plugin Direction

The current release is a local Python/GUI workflow that integrates with Zotero through local DB snapshots, Better BibTeX export, and Zotero Web API write-back. A full Zotero plugin is a separate packaging target. The clean split would be:

- Zotero plugin UI: select items, call the local service, show progress.
- Local Python service: extraction, model calls, packaging, Obsidian export.
- Plugin settings: model provider, API key env names, output folders.

This keeps the heavy PDF/model workflow outside Zotero while giving Zotero a native entry point.

