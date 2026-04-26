"""Remove stale/duplicate folders from obsidian_vault/papers/.

Reads the current sync_state.json to determine which obsidian_stem values
are expected.  Any folder in obsidian_vault/papers/ that is NOT in the
expected set is treated as stale and moved to a timestamped
_trash/<timestamp>/ directory so it can be manually verified before
permanent deletion.

Usage:
    python cleanup_obsidian_duplicates.py [--config config.json] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean stale obsidian_vault/papers/ folders.")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent / "config.json")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would be removed.")
    args = parser.parse_args()

    config_path = args.config.expanduser().resolve()
    raw = load_json(config_path)
    base_dir = config_path.parent

    vault_rel = raw.get("obsidian", {}).get("vault_dir", "obsidian_vault")
    papers_sub = raw.get("obsidian", {}).get("papers_subdir", "papers")
    vault_dir = (base_dir / vault_rel).resolve() if not Path(vault_rel).is_absolute() else Path(vault_rel).resolve()
    papers_root = vault_dir / papers_sub

    state_rel = raw.get("state_path", ".state/sync_state.json")
    state_path = (base_dir / state_rel).resolve() if not Path(state_rel).is_absolute() else Path(state_rel).resolve()

    if not papers_root.exists():
        print(f"Papers root does not exist: {papers_root}")
        return 1

    # ── Collect expected stems from sync_state ──
    state = load_json(state_path)
    items = state.get("items", {})
    expected_stems: set[str] = set()
    for record in items.values():
        if not isinstance(record, dict):
            continue
        obsidian_dir = record.get("obsidian_dir", "")
        if obsidian_dir:
            expected_stems.add(Path(obsidian_dir).name)
        stem = record.get("obsidian_stem") or record.get("summary_stem")
        if stem:
            expected_stems.add(stem)

    # ── Scan actual folders ──
    actual_folders = sorted(
        d for d in papers_root.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )

    stale = [d for d in actual_folders if d.name not in expected_stems]
    kept = [d for d in actual_folders if d.name in expected_stems]

    print(f"Expected stems: {len(expected_stems)}")
    print(f"Actual folders: {len(actual_folders)}")
    print(f"Kept:           {len(kept)}")
    print(f"Stale:          {len(stale)}")
    print()

    if not stale:
        print("Nothing to clean.")
        return 0

    # ── Report ──
    for d in stale:
        print(f"  [STALE] {d.name}")

    if args.dry_run:
        print("\n(dry-run) No changes made.")
        return 0

    # ── Move stale to _trash ──
    trash_dir = papers_root / "_trash" / time.strftime("%Y%m%d-%H%M%S")
    trash_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for d in stale:
        dest = trash_dir / d.name
        try:
            shutil.move(str(d), str(dest))
            moved += 1
        except Exception as exc:
            print(f"  [ERROR] Could not move {d.name}: {exc}")

    print(f"\nMoved {moved} stale folder(s) to {trash_dir}")
    print("Review and delete _trash/ manually when satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
