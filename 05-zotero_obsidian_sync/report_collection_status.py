from __future__ import annotations

import argparse
import copy
import json
import os
import time
from pathlib import Path

import sync_pipeline as sp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan 01-paper PDFs and report which ones are not in the 01-paper-sync Zotero collection."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config.json",
        help="Path to config.json",
    )
    parser.add_argument(
        "--open-report",
        action="store_true",
        help="Open the generated markdown report after scanning.",
    )
    return parser.parse_args()


def clone_config(config: sp.Config, **updates: object) -> sp.Config:
    cloned = copy.deepcopy(config)
    for key, value in updates.items():
        setattr(cloned, key, value)
    return cloned


def load_collection_records(config: sp.Config) -> tuple[list[dict], str]:
    export_records = sp.load_export_records(config.export_path)
    if export_records:
        return export_records, "bbt-export"

    local_config = clone_config(config, zotero_local_fallback_to_all_pdf_items=False)
    local_records = sp.load_export_records_from_local_zotero(local_config)
    return local_records, "local-zotero-collection"


def load_library_records(config: sp.Config) -> tuple[list[dict], str]:
    local_config = clone_config(
        config,
        zotero_local_collection_name="__codex_force_all_pdf_items__",
        zotero_local_fallback_to_all_pdf_items=True,
    )
    local_records = sp.load_export_records_from_local_zotero(local_config)
    return local_records, "local-zotero-all-pdf-items"


def summarize_match(match: dict | None, score: float, reason: str) -> dict[str, object]:
    if not match:
        return {
            "matched": False,
            "title": "",
            "item_key": "",
            "score": round(score, 4),
            "reason": reason,
        }
    return {
        "matched": True,
        "title": match.get("title", ""),
        "item_key": match.get("item_key", ""),
        "score": round(score, 4),
        "reason": reason,
    }


def write_report(
    config: sp.Config,
    results: list[dict[str, object]],
    *,
    collection_source: str,
    library_source: str,
) -> Path:
    reports_dir = config.cache_root / "reports"
    sp.ensure_dir(reports_dir)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    latest_md = reports_dir / "collection-status-latest.md"
    latest_json = reports_dir / "collection-status-latest.json"
    snapshot_md = reports_dir / f"collection-status-{timestamp}.md"
    snapshot_json = reports_dir / f"collection-status-{timestamp}.json"

    in_collection = [row for row in results if row["status"] == "in_collection"]
    in_library_only = [row for row in results if row["status"] == "in_zotero_not_in_collection"]
    missing_everywhere = [row for row in results if row["status"] == "not_in_zotero"]

    lines = [
        "# 01-paper 与 01-paper-sync 集合状态报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 扫描 PDF 数量：{len(results)}",
        f"- 集合索引来源：{collection_source}",
        f"- 全库索引来源：{library_source}",
        f"- 已在 01-paper-sync 中：{len(in_collection)}",
        f"- 已在 Zotero 但不在 01-paper-sync 中：{len(in_library_only)}",
        f"- Zotero 中未发现匹配：{len(missing_everywhere)}",
        "",
        "## 已在 01-paper-sync 中",
        "",
    ]

    if in_collection:
        for row in in_collection:
            lines.append(
                f"- `{row['pdf_name']}` -> `{row['collection_item_key']}` | {row['collection_title']}"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "## 已在 Zotero 但不在 01-paper-sync 中", ""])
    if in_library_only:
        for row in in_library_only:
            lines.append(
                f"- `{row['pdf_name']}` -> `{row['library_item_key']}` | {row['library_title']}"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "## Zotero 中未发现匹配", ""])
    if missing_everywhere:
        for row in missing_everywhere:
            lines.append(f"- `{row['pdf_name']}`")
    else:
        lines.append("- 无")

    lines.extend(
        [
            "",
            "## 建议操作",
            "",
            "- 第二部分里的条目，说明文献大概率已经在 Zotero 里，但还没加入 `01-paper-sync` 集合。",
            "- 第三部分里的条目，说明需要先把 PDF 或元数据加入 Zotero，再放进 `01-paper-sync` 集合。",
            "- 只要条目进入 `01-paper-sync`，当前同步脚本就能优先按该集合建立匹配。",
            "",
        ]
    )

    md_text = "\n".join(lines).rstrip() + "\n"
    json_text = json.dumps(results, ensure_ascii=False, indent=2)

    latest_md.write_text(md_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    snapshot_md.write_text(md_text, encoding="utf-8")
    snapshot_json.write_text(json_text, encoding="utf-8")
    return latest_md


def main() -> int:
    args = parse_args()
    config = sp.load_config(args.config)
    sp.ensure_dir(config.cache_root)

    collection_records, collection_source = load_collection_records(config)
    library_records, library_source = load_library_records(config)
    collection_index = sp.build_export_index(collection_records)
    library_index = sp.build_export_index(library_records)

    results: list[dict[str, object]] = []
    for pdf_path in sp.discover_pdfs(config):
        bundle = sp.load_extraction_bundle(sp.extract_pdf(config, pdf_path, force=False))
        inferred = sp.infer_pdf_metadata(pdf_path, bundle)
        collection_match, collection_score, collection_reason = sp.match_export_record(
            pdf_path,
            inferred,
            collection_index,
        )
        library_match, library_score, library_reason = (None, 0.0, "not-checked")
        status = "in_collection"
        if collection_match is None:
            library_match, library_score, library_reason = sp.match_export_record(
                pdf_path,
                inferred,
                library_index,
            )
            status = "in_zotero_not_in_collection" if library_match else "not_in_zotero"

        collection_info = summarize_match(collection_match, collection_score, collection_reason)
        library_info = summarize_match(library_match, library_score, library_reason)
        results.append(
            {
                "pdf_name": pdf_path.name,
                "pdf_path": str(pdf_path.resolve()),
                "status": status,
                "inferred_title": inferred.get("title", ""),
                "collection_title": collection_info["title"],
                "collection_item_key": collection_info["item_key"],
                "collection_score": collection_info["score"],
                "collection_reason": collection_info["reason"],
                "library_title": library_info["title"],
                "library_item_key": library_info["item_key"],
                "library_score": library_info["score"],
                "library_reason": library_info["reason"],
            }
        )

    report_path = write_report(
        config,
        results,
        collection_source=collection_source,
        library_source=library_source,
    )
    print(f"[done] Report written: {report_path}")
    if args.open_report and os.name == "nt":
        os.startfile(str(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
