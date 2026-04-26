from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import time
from pathlib import Path

import paper_sync_gui as gui
import sync_pipeline as sp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync already packaged summary folders into Obsidian/state without regenerating summaries."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config.json",
        help="Path to config.json",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "changed"),
        default="full",
        help="`full` rebuilds/checks every package and collection item; `changed` only processes items changed since the last recorded sync state.",
    )
    return parser.parse_args()


def clone_config(config: sp.Config, **updates: object) -> sp.Config:
    cloned = copy.deepcopy(config)
    for key, value in updates.items():
        setattr(cloned, key, value)
    return cloned


def load_matching_records(config: sp.Config) -> list[dict]:
    export_records = sp.load_export_records(config.export_path)
    if export_records:
        sp.log(f"[match] Loaded {len(export_records)} records from BBT export.")
        return export_records

    sp.log("[match] BBT export missing or empty; falling back to local Zotero index.")
    return sp.load_export_records_from_local_zotero(config)


def iter_packaged_dirs(workspace_root: Path) -> list[Path]:
    package_dirs: list[Path] = []
    for child in workspace_root.iterdir():
        if not child.is_dir():
            continue
        if (child / "manifest.json").exists():
            package_dirs.append(child.resolve())
    return sorted(package_dirs)


def safe_file_signature(path: Path) -> dict[str, int] | None:
    try:
        if not path.exists():
            return None
        return sp.file_signature(path)
    except OSError:
        return None


def signatures_match(left: object, right: object) -> bool:
    return isinstance(left, dict) and isinstance(right, dict) and left == right


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False


def copy2_robust(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        shutil.copy2(sp._extended_path(src.resolve()), sp._extended_path(dst.resolve()))
        return
    shutil.copy2(src, dst)


def materialize_pdf_source(config: sp.Config, pdf_path: Path, package_dir: Path, summary_stem: str) -> Path:
    if not is_relative_to(pdf_path, package_dir):
        return pdf_path
    cache_dir = config.cache_root / "sync-existing-repackage-pdfs"
    sp.ensure_dir(cache_dir)
    cached_pdf = cache_dir / f"{sp.sanitize_windows_filename(summary_stem, 'package')}{pdf_path.suffix.lower() or '.pdf'}"
    copy2_robust(pdf_path, cached_pdf)
    return cached_pdf.resolve()


def materialize_pdf_sources(config: sp.Config, pdf_paths: list[Path], package_dir: Path, summary_stem: str) -> list[Path]:
    materialized: list[Path] = []
    for index, pdf_path in enumerate(pdf_paths, start=1):
        candidate = materialize_pdf_source(config, pdf_path, package_dir, f"{summary_stem}-{index:02d}")
        materialized.append(candidate)
    return materialized


def materialize_summary_source(
    config: sp.Config,
    summary_path: Path,
    summary_stem: str,
    package_dir: Path | None = None,
) -> Path:
    if package_dir and is_relative_to(summary_path, package_dir):
        return summary_path
    if summary_path.stem == summary_stem:
        return summary_path
    cache_dir = config.cache_root / "sync-existing-summary-sources"
    sp.ensure_dir(cache_dir)
    cached_summary = cache_dir / f"{sp.sanitize_windows_filename(summary_stem, 'summary')}.md"
    if cached_summary.exists() and cached_summary.is_dir():
        shutil.rmtree(cached_summary, ignore_errors=True)
    conflicting_dir = cache_dir / sp.sanitize_windows_filename(summary_stem, "summary")
    if conflicting_dir.exists() and conflicting_dir.is_dir():
        shutil.rmtree(conflicting_dir, ignore_errors=True)
    copy2_robust(summary_path, cached_summary)
    return cached_summary.resolve()


def resolve_summary_path(
    config: sp.Config,
    package_dir: Path,
    manifest: dict,
    previous: dict | None = None,
) -> Path:
    candidates: list[Path] = []
    staged_summary_root = (config.cache_root / "sync-existing-summary-sources").resolve()
    summary_source = manifest.get("summary_source")
    if isinstance(summary_source, str) and summary_source.strip():
        summary_source_path = Path(summary_source)
        if not is_relative_to(summary_source_path, staged_summary_root):
            candidates.append(summary_source_path)

    candidates.append(config.summary_root / f"{package_dir.name}.md")

    packaged_summary = manifest.get("packaged_summary")
    if isinstance(packaged_summary, str) and packaged_summary.strip():
        candidates.append(package_dir / packaged_summary)

    candidates.append(package_dir / f"{package_dir.name}.md")

    previous_summary = str((previous or {}).get("summary_md") or "").strip()
    if previous_summary:
        candidates.append(Path(previous_summary))
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        except OSError:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(f"Summary markdown not found for package: {package_dir}")


def resolve_pdf_paths(
    config: sp.Config,
    package_dir: Path,
    manifest: dict,
    previous: dict | None = None,
) -> list[Path]:
    resolved: list[Path] = []
    previous_pdf = str((previous or {}).get("pdf_path") or "").strip()

    pdfs = manifest.get("pdfs")
    if isinstance(pdfs, list):
        for pdf in pdfs:
            if not isinstance(pdf, dict):
                continue
            candidates: list[Path] = []
            source_pdf = pdf.get("source_pdf")
            if isinstance(source_pdf, str) and source_pdf.strip():
                candidate = Path(source_pdf)
                candidates.append(candidate)
                basename = candidate.name
                if basename:
                    candidates.append(config.paper_dir / basename)
            packaged_pdf = pdf.get("packaged_pdf")
            if isinstance(packaged_pdf, str) and packaged_pdf.strip():
                candidates.append(package_dir / packaged_pdf)
            seen: set[str] = set()
            for candidate in candidates:
                try:
                    key = str(candidate.resolve()) if candidate.exists() else str(candidate)
                except OSError:
                    key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                if candidate.exists():
                    resolved.append(candidate.resolve())
                    break
    if resolved:
        return resolved
    if previous_pdf:
        candidate = Path(previous_pdf)
        if candidate.exists():
            return [candidate.resolve()]
    raise FileNotFoundError("No existing source_pdf found in manifest.")


def package_requires_sync(
    previous: dict | None,
    package_dir: Path,
    summary_path: Path,
    pdf_paths: list[Path],
) -> bool:
    if not isinstance(previous, dict):
        return True
    if str(previous.get("summary_stem") or "") != package_dir.name:
        return True
    if not signatures_match(previous.get("summary_md_sig"), safe_file_signature(summary_path)):
        return True
    current_pdf_sigs = [safe_file_signature(path) for path in pdf_paths]
    previous_pdf_sigs = previous.get("pdf_sigs")
    if isinstance(previous_pdf_sigs, list) and previous_pdf_sigs:
        if len(previous_pdf_sigs) != len(current_pdf_sigs):
            return True
        for left, right in zip(previous_pdf_sigs, current_pdf_sigs):
            if not signatures_match(left, right):
                return True
    else:
        if len(current_pdf_sigs) != 1:
            return True
        if not signatures_match(previous.get("pdf_sig"), current_pdf_sigs[0]):
            return True
    if not signatures_match(previous.get("manifest_sig"), safe_file_signature(package_dir / "manifest.json")):
        return True
    obsidian_dir = str(previous.get("obsidian_dir") or "").strip()
    if obsidian_dir and not Path(obsidian_dir).exists():
        return True
    obsidian_md = str(previous.get("obsidian_md") or "").strip()
    if obsidian_md and not Path(obsidian_md).exists():
        return True
    return False


def collection_item_requires_sync(
    config: sp.Config,
    item: dict[str, object],
    existing: dict[str, object] | None,
) -> bool:
    if not isinstance(existing, dict):
        return True

    preferred_pdf = sp.preferred_record_pdf_path(config, item.get("preferred_pdf"), existing.get("pdf_path"))

    comparisons = (
        ("title", item.get("title"), existing.get("title")),
        ("year", item.get("year"), existing.get("year")),
        ("source", item.get("source"), existing.get("source")),
        ("doi", item.get("doi"), existing.get("doi")),
        ("arxiv", item.get("arxiv"), existing.get("arxiv")),
        ("citation_key", item.get("citation_key"), existing.get("citation_key")),
        ("item_key", item.get("item_key"), existing.get("zotero_item_key")),
        ("date_added", item.get("date_added"), existing.get("date_added")),
        ("date_modified", item.get("date_modified"), existing.get("date_modified")),
        ("preferred_pdf", preferred_pdf, existing.get("pdf_path")),
    )
    for _, left, right in comparisons:
        if str(left or "") != str(right or ""):
            return True

    if list(item.get("tags") or []) != list(existing.get("tags") or []):
        return True
    if list(item.get("authors") or []) != list(existing.get("authors") or []):
        return True
    if list(item.get("creators") or []) != list(existing.get("creators") or []):
        return True

    summary_attached = bool(item.get("summary_attached"))
    existing_summary_attached = bool(existing.get("summary_md")) and Path(str(existing.get("summary_md"))).exists()
    if summary_attached != existing_summary_attached:
        return True

    obsidian_dir = str(existing.get("obsidian_dir") or "").strip()
    obsidian_md = str(existing.get("obsidian_md") or "").strip()
    if obsidian_dir and not Path(obsidian_dir).exists():
        return True
    if obsidian_md and not Path(obsidian_md).exists():
        return True

    return False


def build_record(
    config: sp.Config,
    package_dir: Path,
    manifest: dict,
    *,
    export_index: dict[str, dict[str, list[dict]]],
    previous: dict | None,
) -> dict[str, object]:
    desired_stem = str((previous or {}).get("summary_stem") or package_dir.name)
    summary_md = resolve_summary_path(config, package_dir, manifest, previous)
    repackage_summary_md = materialize_summary_source(config, summary_md, desired_stem, package_dir)
    original_pdf_paths = resolve_pdf_paths(config, package_dir, manifest, previous)
    repackage_pdf_paths = materialize_pdf_sources(config, original_pdf_paths, package_dir, desired_stem)
    package_dir = sp.package_summary(config, repackage_summary_md, repackage_pdf_paths, clean=True)

    primary_pdf_path = original_pdf_paths[0]
    bundle = sp.load_extraction_bundle(sp.extract_pdf(config, primary_pdf_path, force=False))
    inferred = sp.infer_pdf_metadata(primary_pdf_path, bundle)
    matched, match_score, match_reason = sp.match_export_record(primary_pdf_path, inferred, export_index)
    metadata = sp.enrich_record_with_match(config, primary_pdf_path, inferred, matched, previous)

    record = {
        "pdf_path": str(primary_pdf_path),
        "pdf_sig": sp.file_signature(primary_pdf_path),
        "pdf_sigs": [sp.file_signature(path) for path in original_pdf_paths],
        "pdf_sha256": sp.sha256_file(primary_pdf_path),
        "summary_md": str(summary_md),
        "package_dir": str(package_dir),
        "summary_stem": package_dir.name,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "match_score": round(match_score, 4),
        "match_reason": match_reason,
        "inferred_title": inferred.get("title", ""),
        "summary_md_sig": safe_file_signature(summary_md),
        "manifest_sig": safe_file_signature(package_dir / "manifest.json"),
        **metadata,
    }
    record["obsidian_stem"] = sp.derive_obsidian_item_stem(record)
    record = sp.repair_record_paths(config, record) or record
    record = sp.sync_obsidian_package(config, record, copy_package=True)
    sp.sync_zotero_record(config, record)
    return record


def state_key_for_record(record: dict[str, object]) -> str:
    pdf_path = str(record.get("pdf_path") or "").strip()
    if pdf_path:
        return str(Path(pdf_path).resolve())
    item_key = str(record.get("zotero_item_key") or "").strip()
    return f"zotero:{item_key}"


def _nonempty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _path_value_exists(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return Path(value).exists()
    except OSError:
        return False


def _pdf_prefers_paper_dir(config: sp.Config, record: dict[str, object]) -> bool:
    pdf_path = str(record.get("pdf_path") or "").strip()
    if not pdf_path:
        return False
    try:
        return is_relative_to(Path(pdf_path), config.paper_dir)
    except OSError:
        return False


def record_rank(config: sp.Config, record: dict[str, object]) -> tuple[int, int, int, int, int, int]:
    return (
        1 if _path_value_exists(record.get("summary_md")) else 0,
        1 if _path_value_exists(record.get("package_dir")) else 0,
        1 if _path_value_exists(record.get("obsidian_md")) else 0,
        1 if _pdf_prefers_paper_dir(config, record) else 0,
        1 if _path_value_exists(record.get("summary_attachment_path")) else 0,
        len(str(record.get("title") or "")),
    )


def merge_records(config: sp.Config, left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    preferred = left if record_rank(config, left) >= record_rank(config, right) else right
    fallback = right if preferred is left else left
    merged = dict(preferred)

    for key, value in fallback.items():
        current = merged.get(key)
        if not _nonempty(current) and _nonempty(value):
            merged[key] = value

    if _pdf_prefers_paper_dir(config, fallback) and not _pdf_prefers_paper_dir(config, preferred):
        for key in ("pdf_path", "pdf_sig", "pdf_sha256"):
            if _nonempty(fallback.get(key)):
                merged[key] = fallback.get(key)

    for key in ("tags", "authors", "obsidian_tags", "ai_tags"):
        values: list[object] = []
        for candidate in (preferred.get(key), fallback.get(key)):
            if isinstance(candidate, list):
                values.extend(candidate)
        if values:
            merged[key] = list(dict.fromkeys(values))

    if isinstance(preferred.get("creators"), list) and preferred.get("creators"):
        merged["creators"] = preferred["creators"]
    elif isinstance(fallback.get("creators"), list) and fallback.get("creators"):
        merged["creators"] = fallback["creators"]

    return sp.repair_record_paths(config, merged) or merged


def compact_state_items(config: sp.Config, state_items: dict[str, object]) -> dict[str, dict[str, object]]:
    groups: list[dict[str, object]] = []
    key_to_index: dict[str, int] = {}

    for raw_record in state_items.values():
        if not isinstance(raw_record, dict):
            continue
        record = sp.repair_record_paths(config, raw_record) or raw_record
        dedupe_keys: list[str] = []
        item_key = str(record.get("zotero_item_key") or "").strip()
        if item_key:
            dedupe_keys.append(f"item:{item_key}")
        summary_stem = str(record.get("summary_stem") or "").strip()
        if summary_stem:
            dedupe_keys.append(f"stem:{summary_stem}")
        obsidian_md = str(record.get("obsidian_md") or "").strip()
        if obsidian_md:
            dedupe_keys.append(f"md:{obsidian_md}")

        existing_indexes = [key_to_index[key] for key in dedupe_keys if key in key_to_index]
        if existing_indexes:
            keep_index = existing_indexes[0]
            merged = groups[keep_index]
            for extra_index in existing_indexes[1:]:
                if extra_index == keep_index:
                    continue
                merged = merge_records(config, merged, groups[extra_index])
                groups[extra_index] = {}
            merged = merge_records(config, merged, record)
            groups[keep_index] = merged
            for key in dedupe_keys:
                key_to_index[key] = keep_index
            continue

        keep_index = len(groups)
        groups.append(record)
        for key in dedupe_keys:
            key_to_index[key] = keep_index

    compacted: dict[str, dict[str, object]] = {}
    for record in groups:
        if not record:
            continue
        compacted[state_key_for_record(record)] = record
    return compacted


def merge_collection_item_into_record(
    config: sp.Config,
    item: dict[str, object],
    existing: dict[str, object] | None,
) -> dict[str, object]:
    record = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    record = sp.repair_record_paths(config, record) or record
    title = str(item.get("title") or record.get("title") or item.get("item_key") or "Untitled")
    tags = list(item.get("tags") or record.get("tags") or [])
    creators = list(item.get("creators") or record.get("creators") or [])
    authors = list(item.get("authors") or record.get("authors") or sp.display_authors_from_creators(creators))
    obsidian_tags = [f"{config.obsidian_tag_prefix}/{sp.sanitize_obsidian_tag(tag)}" for tag in tags]
    pdf_path = sp.preferred_record_pdf_path(config, item.get("preferred_pdf"), record.get("pdf_path"))

    record.update(
        {
            "title": title,
            "year": str(item.get("year") or record.get("year") or ""),
            "source": str(item.get("source") or record.get("source") or ""),
            "doi": str(item.get("doi") or record.get("doi") or ""),
            "arxiv": str(item.get("arxiv") or record.get("arxiv") or ""),
            "tags": tags,
            "creators": creators,
            "authors": authors,
            "obsidian_tags": sorted(dict.fromkeys(obsidian_tags)),
            "citation_key": str(item.get("citation_key") or record.get("citation_key") or ""),
            "zotero_item_key": str(item.get("item_key") or record.get("zotero_item_key") or ""),
            "date_added": str(item.get("date_added") or record.get("date_added") or ""),
            "date_modified": str(item.get("date_modified") or record.get("date_modified") or ""),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    if pdf_path:
        pdf = Path(pdf_path)
        if pdf.exists():
            record["pdf_path"] = str(pdf.resolve())
            record["pdf_sig"] = sp.file_signature(pdf)
            record["pdf_sha256"] = sp.sha256_file(pdf)
    summary_children = list(item.get("summary_children") or [])
    summary_attachment_path = ""
    for child in summary_children:
        abs_path = str(child.get("abs_path") or "").strip()
        if abs_path and abs_path.lower().endswith(".md") and Path(abs_path).exists():
            summary_attachment_path = str(Path(abs_path).resolve())
            break
    if summary_attachment_path:
        record["summary_attachment_path"] = summary_attachment_path
        current_summary = str(record.get("summary_md") or "").strip()
        if not current_summary or not Path(current_summary).exists():
            record["summary_md"] = summary_attachment_path
    if not record.get("summary_stem"):
        record["summary_stem"] = sp.derive_obsidian_item_stem(record)
    record["obsidian_stem"] = sp.derive_obsidian_item_stem(record)
    return record


def main() -> int:
    args = parse_args()
    config = sp.load_config(args.config)
    sp.ensure_dir(config.cache_root)
    sp.ensure_dir(config.obsidian_vault_dir)
    sp.ensure_dir(config.obsidian_vault_dir / config.obsidian_papers_subdir)
    sp.ensure_dir(config.obsidian_vault_dir / config.obsidian_tags_subdir)

    export_records = load_matching_records(config)
    export_index = sp.build_export_index(export_records)
    state = sp.load_json(config.state_path, {"spec_hash": "", "items": {}})
    state_items = state.get("items", {})
    if not isinstance(state_items, dict):
        state_items = {}

    processed: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    new_state_items = dict(state_items)
    package_dirs = iter_packaged_dirs(config.workspace_root)
    packages_checked = 0
    packages_skipped = 0
    collection_checked = 0
    collection_skipped = 0
    previous_by_summary_stem = {
        str(record.get("summary_stem")): record
        for record in state_items.values()
        if isinstance(record, dict) and record.get("summary_stem")
    }

    for package_dir in package_dirs:
        manifest_path = package_dir / "manifest.json"
        manifest = sp.load_json(manifest_path, {})
        try:
            previous = previous_by_summary_stem.get(package_dir.name)
            summary_path = resolve_summary_path(config, package_dir, manifest, previous if isinstance(previous, dict) else None)
            pdf_paths = resolve_pdf_paths(config, package_dir, manifest, previous if isinstance(previous, dict) else None)
            packages_checked += 1
            if args.mode == "changed" and not package_requires_sync(
                previous if isinstance(previous, dict) else None,
                package_dir,
                summary_path,
                pdf_paths,
            ):
                packages_skipped += 1
                sp.log(f"[sync-existing-skip] {package_dir.name}")
                if isinstance(previous, dict):
                    new_state_items[state_key_for_record(previous)] = previous
                continue
            record = build_record(
                config,
                package_dir,
                manifest,
                export_index=export_index,
                previous=previous if isinstance(previous, dict) else None,
            )
            new_state_items[str(pdf_paths[0].resolve())] = record
            processed.append(record)
            sp.log(f"[sync-existing] {package_dir.name}")
        except Exception as exc:
            sp.log(f"[sync-existing-error] {package_dir.name}: {exc}")
            errors.append({"package_dir": package_dir.name, "error": str(exc)})

    new_state_items = compact_state_items(config, new_state_items)
    state_by_item_key = {
        str(record.get("zotero_item_key")): record
        for record in new_state_items.values()
        if isinstance(record, dict) and record.get("zotero_item_key")
    }
    collection_items = gui.load_collection_items(config, sorted(config.paper_dir.rglob("*.pdf")))
    metadata_only_count = 0
    metadata_updated_count = 0
    for item in collection_items:
        collection_checked += 1
        existing = state_by_item_key.get(str(item.get("item_key") or ""))
        if args.mode == "changed" and not collection_item_requires_sync(
            config,
            item,
            existing if isinstance(existing, dict) else None,
        ):
            collection_skipped += 1
            continue
        record = merge_collection_item_into_record(
            config,
            item,
            existing if isinstance(existing, dict) else None,
        )
        try:
            summary_md = str(record.get("summary_md") or "").strip()
            package_dir_value = str(record.get("package_dir") or "").strip()
            package_dir_path = Path(package_dir_value) if package_dir_value else None
            package_exists = package_dir_path is not None and package_dir_path.exists()
            if summary_md and Path(summary_md).exists():
                source_summary = Path(summary_md)
                if not package_exists:
                    pdf_path_value = str(record.get("pdf_path") or "").strip()
                    if pdf_path_value and Path(pdf_path_value).exists():
                        desired_stem = str(record.get("summary_stem") or sp.derive_obsidian_item_stem(record))
                        staged_summary = materialize_summary_source(config, source_summary, desired_stem)
                        package_dir = sp.package_summary(config, staged_summary, Path(pdf_path_value), clean=True)
                        record["package_dir"] = str(package_dir)
                        record["summary_stem"] = package_dir.name
                        record["summary_md_sig"] = safe_file_signature(source_summary)
                        record["manifest_sig"] = safe_file_signature(package_dir / "manifest.json")
                        record["obsidian_stem"] = sp.derive_obsidian_item_stem(record)
                        record = sp.sync_obsidian_package(config, record, copy_package=True)
                        metadata_updated_count += 1
                    else:
                        record = sp.sync_obsidian_metadata_record(config, record)
                        metadata_only_count += 1
                else:
                    record["summary_md_sig"] = safe_file_signature(source_summary)
                    record["manifest_sig"] = safe_file_signature(package_dir_path / "manifest.json")
                    record = sp.sync_obsidian_package(config, record, copy_package=False)
                    metadata_updated_count += 1
            else:
                record = sp.sync_obsidian_metadata_record(config, record)
                metadata_only_count += 1
            new_state_items[state_key_for_record(record)] = record
            if record.get("summary_md") and Path(str(record.get("summary_md"))).exists():
                sp.sync_zotero_record(config, record)
        except Exception as exc:
            item_key = str(item.get("item_key") or "")
            sp.log(f"[sync-collection-error] {item_key}: {exc}")
            errors.append({"zotero_item_key": item_key, "error": str(exc)})

    compacted_state_items = compact_state_items(config, new_state_items)
    all_records = [record for record in compacted_state_items.values() if isinstance(record, dict)]
    sp.rebuild_obsidian_indexes(config, all_records)
    sp.save_json(
        config.state_path,
        {
            "spec_hash": sp.sha256_text(sp.read_text(config.spec_path)) if config.spec_path.exists() else "",
            "items": compacted_state_items,
            "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sync_existing_packages_last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    report = {
        "mode": args.mode,
        "packages_checked": packages_checked,
        "processed_packages": len(processed),
        "packages_skipped": packages_skipped,
        "package_dirs": [record["summary_stem"] for record in processed],
        "collection_checked": collection_checked,
        "collection_skipped": collection_skipped,
        "collection_items_total": len(collection_items),
        "metadata_only_count": metadata_only_count,
        "metadata_updated_count": metadata_updated_count,
        "errors": errors,
    }
    report_path = config.cache_root / "sync-existing-packages-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sp.log(
        f"[done] Mode={args.mode}; packages synced: {len(processed)} / {packages_checked}; "
        f"collection metadata exported: {metadata_only_count} new / {metadata_updated_count} updated; "
        f"skipped: {packages_skipped} packages, {collection_skipped} collection items."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
