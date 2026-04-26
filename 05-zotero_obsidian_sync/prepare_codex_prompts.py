from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sync_pipeline import (
    Config,
    ascii_slug,
    build_export_index,
    derive_summary_stem,
    ensure_dir,
    extract_pdf,
    infer_pdf_metadata,
    load_config,
    load_export_records,
    load_extraction_bundle,
    match_export_record,
    read_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Codex-ready prompt files for PDFs when no OPENAI_API_KEY is available."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config.json",
        help="Path to config.json.",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        nargs="*",
        help="Optional explicit PDF paths. Defaults to all PDFs under paper_dir.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of prompt files to generate in this run. 0 means no limit.",
    )
    return parser.parse_args()


def prompt_dir(config: Config) -> Path:
    return config.config_path.parent / "manual_queue"


def discover_pdfs(config: Config, explicit: list[Path] | None) -> list[Path]:
    if explicit:
        return [path.expanduser().resolve() for path in explicit]
    return sorted(config.paper_dir.rglob("*.pdf"))


def build_prompt(
    config: Config,
    pdf_path: Path,
    extraction_dir: Path,
    summary_stem: str,
    matched: dict | None,
) -> str:
    spec_text = read_text(config.spec_path)
    metadata = {
        "pdf_path": str(pdf_path.resolve()),
        "suggested_summary_md": str((config.summary_root / f"{summary_stem}.md").resolve()),
        "suggested_package_dir": str((config.summary_root / summary_stem).resolve()),
        "spec_path": str(config.spec_path.resolve()),
        "extraction_dir": str(extraction_dir.resolve()),
        "matched_zotero_metadata": {
            "title": matched.get("title"),
            "year": matched.get("year"),
            "source": matched.get("source"),
            "creators": matched.get("creators"),
            "tags": matched.get("tags"),
            "citation_key": matched.get("citation_key"),
            "item_key": matched.get("item_key"),
        }
        if matched
        else None,
    }

    return (
        "请在当前工作区中直接完成这篇论文的完整总结与打包。\n\n"
        "任务要求：\n"
        "1. 重新读取最新默认规范文件，不要沿用旧记忆。\n"
        "2. 按默认规范生成中文 Markdown 总结。\n"
        "3. 行内公式必须使用 $...$，不要用反引号包公式。\n"
        "4. 必须包含摘要翻译，并紧跟在一句话结论之后。\n"
        "5. Methods / 附录 / 补充材料中的关键方法学内容必须详细分析。\n"
        "6. 总结完成后，必须调用现有 paper workflow 打包。\n\n"
        f"辅助元数据：\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n\n"
        "建议的最短执行方式：\n"
        f"- 目标 PDF：`{pdf_path}`\n"
        f"- 提取目录：`{extraction_dir}`\n"
        f"- 规范文件：`{config.spec_path}`\n"
        f"- 建议输出 Markdown：`{config.summary_root / (summary_stem + '.md')}`\n\n"
        "最新默认规范全文如下：\n\n"
        f"{spec_text}\n"
    )


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    pdir = prompt_dir(config)
    ensure_dir(pdir)

    export_records = load_export_records(config.export_path)
    export_index = build_export_index(export_records)

    pdfs = discover_pdfs(config, args.pdf)
    generated: list[dict[str, str]] = []

    for pdf_path in pdfs:
        if not pdf_path.exists():
            continue
        extraction_dir = extract_pdf(config, pdf_path, force=False)
        bundle = load_extraction_bundle(extraction_dir)
        inferred = infer_pdf_metadata(pdf_path, bundle)
        matched, _, _ = match_export_record(pdf_path, inferred, export_index)
        summary_stem = derive_summary_stem(pdf_path, inferred, matched)

        prompt_text = build_prompt(config, pdf_path, extraction_dir, summary_stem, matched)
        prompt_path = pdir / f"{ascii_slug(summary_stem)}.prompt.txt"
        packaged_dir = config.summary_root / summary_stem
        if packaged_dir.exists():
            continue
        prompt_path.write_text(prompt_text, encoding="utf-8")
        generated.append(
            {
                "pdf": str(pdf_path.resolve()),
                "prompt": str(prompt_path.resolve()),
                "summary_md": str((config.summary_root / f"{summary_stem}.md").resolve()),
            }
        )
        if args.limit and len(generated) >= args.limit:
            break

    index_path = pdir / "index.json"
    index_path.write_text(json.dumps(generated, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"count": len(generated), "index": str(index_path.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
