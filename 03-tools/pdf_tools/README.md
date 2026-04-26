# PDF Tools

This directory contains a local, reusable PDF parsing environment managed by `uv`.

## What It Produces

For each input PDF, the extractor writes:

- `document.txt`: plain text with page separators
- `document.md`: markdown extracted from the PDF
- `assets/`: formula and figure snippets referenced by `document.md`
- `metadata.json`: PDF metadata and extraction summary
- `pages/`: one rendered PNG per page
- `embedded/`: raster images embedded in the PDF, if any

The extractor now prefers the Docling Serve instance at `http://192.168.31.211:5001` and falls back to local parsing when the service is unavailable or times out.

## First-Time Setup

Run this once from this directory:

```powershell
uv sync
```

`uv` will install a local Python runtime if needed and create `.venv`.

## Usage

From this directory:

```powershell
uv run python .\extract_pdf.py ..\..\01-paper\2507.10356v2.pdf --clean
```

Or use the wrapper:

```powershell
.\extract_pdf.ps1 ..\..\01-paper\2507.10356v2.pdf -Clean
```

To choose a custom output directory:

```powershell
.\extract_pdf.ps1 ..\..\01-paper\2507.10356v2.pdf ..\..\01-paper\parsed\2507.10356v2 -Clean
```

## Notes

- This setup works best for born-digital PDFs.
- `document.md` no longer appends `pages/page-xxx.png` links. Page renders are still written under `pages/` for debugging, but they are not exposed as normal figure candidates to later summary generation.
- Mathematical expressions should still be spot-checked even when Docling formula enrichment is enabled.
- Vector figures are still preserved in rendered page images under `pages/`, but those page renders are now a debug artifact rather than a preferred summary image source.
