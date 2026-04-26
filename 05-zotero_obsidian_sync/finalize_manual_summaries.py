from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sync_pipeline import (
    build_export_index,
    enrich_record_with_match,
    extract_pdf,
    infer_pdf_metadata,
    load_config,
    load_export_records,
    load_extraction_bundle,
    match_export_record,
    rebuild_obsidian_indexes,
    save_json,
    sha256_file,
    sync_obsidian_package,
    sync_zotero_record,
    file_signature,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize manually generated summaries by syncing packaged folders to Obsidian/Zotero."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config.json",
        help="Path to config.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    queue_index = config.config_path.parent / "manual_queue" / "index.json"
    if not queue_index.exists():
        raise FileNotFoundError(f"Manual queue index not found: {queue_index}")

    entries = json.loads(queue_index.read_text(encoding="utf-8"))
    export_records = load_export_records(config.export_path)
    export_index = build_export_index(export_records)

    completed: list[dict] = []
    state_items: dict[str, dict] = {}

    for entry in entries:
        pdf_path = Path(entry["pdf"])
        summary_md = Path(entry["summary_md"])
        package_dir = summary_md.with_suffix("")
        if not pdf_path.exists() or not summary_md.exists() or not package_dir.exists():
            continue

        bundle = load_extraction_bundle(extract_pdf(config, pdf_path, force=False))
        inferred = infer_pdf_metadata(pdf_path, bundle)
        matched, match_score, match_reason = match_export_record(pdf_path, inferred, export_index)
        metadata = enrich_record_with_match(config, pdf_path, inferred, matched, None)

        record = {
            "pdf_path": str(pdf_path.resolve()),
            "pdf_sig": file_signature(pdf_path),
            "pdf_sha256": sha256_file(pdf_path),
            "summary_md": str(summary_md.resolve()),
            "package_dir": str(package_dir.resolve()),
            "summary_stem": summary_md.stem,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "match_score": round(match_score, 4),
            "match_reason": match_reason,
            "inferred_title": inferred.get("title", ""),
            **metadata,
        }
        record = sync_obsidian_package(config, record, copy_package=True)
        sync_zotero_record(config, record)
        completed.append(record)
        state_items[str(pdf_path.resolve())] = record

    rebuild_obsidian_indexes(config, completed)
    save_json(
        config.state_path,
        {
            "spec_hash": "",
            "items": state_items,
            "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "manual-finalize",
        },
    )
    print(json.dumps({"completed": len(completed)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
