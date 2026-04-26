# Paper Summary Workflow

This directory contains the reusable packaging step for paper summaries.

## Summary Specs

Default summary spec:

- [default.md](/CsOT_NAS/homesCsOT/CsOT_Record/读文章/wzy/02-paper_summary_specs/default.md)

Template for your own spec:

- [template.md](/CsOT_NAS/homesCsOT/CsOT_Record/读文章/wzy/02-paper_summary_specs/template.md)

Default multi-paper comparison spec:

- [compare.md](/CsOT_NAS/homesCsOT/CsOT_Record/读文章/wzy/02-paper_summary_specs/compare.md)

Specs folder:

- [02-paper_summary_specs](/CsOT_NAS/homesCsOT/CsOT_Record/读文章/wzy/02-paper_summary_specs)

## Goal

Given:

- one or more source PDFs
- one markdown summary written by Codex

the tool produces a self-contained package folder under `wzy/` whose:

- folder name equals the markdown filename stem
- packaged markdown keeps the same filename
- source PDFs are copied into `sources/`
- parsed PDF materials are written into `extracted/`
- markdown-referenced images are copied into `images/`
- an optional appendix with auto-extracted formulas and figures is appended to the packaged markdown
- a `manifest.json` records what was copied

## Recommended User Flow

1. Put the original PDFs under `01-paper/` or any known local path.
2. Before writing the summary, reload the current spec file from disk:
   - default flow: `02-paper_summary_specs/default.md`
   - named spec flow: `02-paper_summary_specs/<name>.md`
3. Ask Codex to summarize the specified PDF(s). By default, the markdown name is auto-generated following the current naming style.
4. Ask Codex to package the result with this tool.

Recommended request pattern:

```text
按默认规范总结并打包 01-paper/foo.pdf [和 01-paper/bar.pdf]
```

Recommended request pattern with an explicit spec file:

```text
按规范 <name> 总结并打包 01-paper/foo.pdf [和 01-paper/bar.pdf]
```

Shortest practical command:

```text
按规范 <name> 总结并打包 <pdf1> [和 <pdf2>...]
```

Example:

```text
按默认规范总结并打包 01-paper/2507.10356v2.pdf
```

Custom spec example:

```text
按规范 compact 总结并打包 01-paper/2507.10356v2.pdf
```

Multi-paper comparison:

```text
在 GUI 中选择两篇或更多 Zotero collection items，然后点击 Compare / 对比总结。
```

The command-line equivalent is:

```powershell
.\03-tools\pdf_tools\.venv\Scripts\python.exe `
  .\05-zotero_obsidian_sync\sync_pipeline.py `
  compare `
  --config .\05-zotero_obsidian_sync\config.json `
  --summary-user-request "重点比较实验平台、关键图和后续可扩展性" `
  --pdf .\01-paper\a.pdf .\01-paper\b.pdf
```

Comparison packages are written under `06-paper_comparisons/`. The workflow also writes `related_papers.json` and tries to add backlinks from existing single-paper Markdown/Obsidian notes and Zotero comparison notes.

## Package Layout

```text
<name>/
  <name>.md
  manifest.json
  sources/
    01-<pdf-stem>.pdf
    02-<pdf-stem>.pdf
  extracted/
    01-<pdf-stem>/
      document.md
      document.txt
      metadata.json
      assets/
      embedded/
      pages/
  images/
    ...
```

## Commands

From the workspace root:

```powershell
.\03-tools\paper_workflow\package_summary.ps1 `
  -SummaryMd .\Some-Paper-Summary.md `
  -SpecPath .\02-paper_summary_specs\default.md `
  -PdfPaths .\01-paper\foo.pdf `
  -Clean
```

For a summary that still needs formulas and figures appended automatically from the PDF extraction:

```powershell
.\03-tools\paper_workflow\package_summary.ps1 `
  -SummaryMd .\Some-Paper-Summary.md `
  -SpecPath .\02-paper_summary_specs\default.md `
  -PdfPaths .\01-paper\foo.pdf `
  -AppendAutoMaterials `
  -Clean
```

For multiple PDFs:

```powershell
.\03-tools\paper_workflow\package_summary.ps1 `
  -SummaryMd .\Combined-Summary.md `
  -SpecPath .\02-paper_summary_specs\<name>.md `
  -PdfPaths .\01-paper\foo.pdf, .\01-paper\bar.pdf `
  -AppendAutoMaterials `
  -Clean
```

If PowerShell execution policy blocks local `.ps1` files, run the Python entry directly:

```powershell
.\03-tools\pdf_tools\.venv\Scripts\python.exe `
  .\03-tools\paper_workflow\package_summary.py `
  .\Some-Paper-Summary.md `
  --spec .\02-paper_summary_specs\default.md `
  --pdf .\01-paper\foo.pdf `
  --append-auto-materials `
  --clean
```

## Notes

- The PDF extraction step reuses `03-tools/pdf_tools/extract_pdf.py`.
- The extractor prefers Docling Serve first and falls back automatically to the local backend if Docling is unavailable.
- The packaging step performs basic summary checks against the active spec file before writing:
  - `## 摘要翻译` must appear immediately after `## 一句话结论`
  - suspicious inline math written with backticks is rejected; inline math should use `$...$`
- If the markdown already contains curated formulas and figures, package without `-AppendAutoMaterials`.
- If the source is PDF-only, `-AppendAutoMaterials` is the safer default. It appends extracted formulas and figures to the packaged markdown, but complex formulas should still be spot-checked.
- Auto-appended figures now only use extracted figure assets. The previous whole-page `pages/page-xxx.png` fallback is intentionally disabled to avoid packaging an entire PDF page as a representative figure.
