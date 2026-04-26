"""Reorganize existing Obsidian paper folders to the new canonical layout.

Target layout for each paper folder::

    paper-folder/
    ├── Title of Paper.pdf          # Main Zotero PDF
    ├── paper-folder.md             # Main summary / metadata stub
    ├── extra-summary.md            # (optional additional MDs)
    └── _attachments/               # Single folder for everything else
        ├── manifest.json
        ├── extracted/...
        ├── images/...
        ├── sources/...
        └── (other misc files)

Usage::

    python reorganize_obsidian_papers.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

ATTACHMENTS = "_attachments"

# Directories that always belong inside _attachments
KNOWN_SUBDIRS = {"extracted", "images", "sources"}

# Files that always belong inside _attachments
KNOWN_ATTACHMENT_FILES = {"manifest.json", "Thumbs.db", "thumbs.db", ".DS_Store"}


def rewrite_attachment_refs(text: str) -> str:
    """Prepend _attachments/ to relative image/link/code refs."""
    text = re.sub(
        r'\]\((?!_attachments/)(images/|extracted/|sources/)',
        lambda m: f"]({ATTACHMENTS}/{m.group(1)}",
        text,
    )
    text = re.sub(
        r'`(?!_attachments/)(sources/[^`]+)`',
        lambda m: f"`{ATTACHMENTS}/{m.group(1)}`",
        text,
    )
    return text


def identify_main_pdf(paper_dir: Path, frontmatter_pdf_name: str) -> str | None:
    """Return the filename of the primary PDF (the Zotero alias) or None."""
    # Try matching by frontmatter obsidian_pdf or pdf_path title
    pdfs = [f for f in paper_dir.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"]
    if not pdfs:
        return None
    if len(pdfs) == 1:
        return pdfs[0].name
    # If the frontmatter-derived name matches, use it
    if frontmatter_pdf_name:
        for p in pdfs:
            if p.name == frontmatter_pdf_name:
                return p.name
    # Fallback: the PDF whose stem best matches the paper title
    # (longest name is usually the Zotero alias with full title)
    pdfs.sort(key=lambda p: len(p.name), reverse=True)
    return pdfs[0].name


def read_frontmatter_field(md_path: Path, field: str) -> str:
    """Extract a single YAML frontmatter field value from an MD file."""
    try:
        text = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---\n", 4)
    if end == -1:
        return ""
    fm = text[4 : end]
    for line in fm.splitlines():
        if line.startswith(f"{field}:"):
            val = line[len(field) + 1 :].strip().strip('"').strip("'")
            return val
    return ""


def reorganize_folder(paper_dir: Path, *, dry_run: bool = False) -> dict:
    """Reorganize one paper folder to the new layout. Returns a report dict."""
    att_dir = paper_dir / ATTACHMENTS
    report: dict = {"folder": paper_dir.name, "actions": [], "errors": []}

    # Already reorganized?
    if att_dir.exists():
        # Check if there are still old-layout dirs at root (partial migration)
        stale = [d for d in KNOWN_SUBDIRS if (paper_dir / d).is_dir()]
        if not stale:
            report["actions"].append("already-reorganized")
            return report

    # Identify the main summary MD
    stem = paper_dir.name
    main_md = paper_dir / f"{stem}.md"

    # Identify the primary PDF name from frontmatter
    obsidian_pdf_name = ""
    if main_md.exists():
        obsidian_pdf_raw = read_frontmatter_field(main_md, "obsidian_pdf") or ""
        if obsidian_pdf_raw:
            obsidian_pdf_name = Path(obsidian_pdf_raw).name
    main_pdf_name = identify_main_pdf(paper_dir, obsidian_pdf_name)

    # Classify every item in the paper folder
    items_to_move: list[Path] = []
    for item in sorted(paper_dir.iterdir()):
        name = item.name
        if name == ATTACHMENTS:
            continue  # skip the target dir itself
        if name.startswith("."):
            continue  # skip hidden

        # Keep .md files at root
        if item.is_file() and item.suffix.lower() == ".md":
            continue

        # Keep the main PDF at root
        if item.is_file() and name == main_pdf_name:
            continue

        # Everything else moves to _attachments
        items_to_move.append(item)

    if not items_to_move:
        report["actions"].append("nothing-to-move")
        return report

    # Create _attachments
    if not dry_run:
        att_dir.mkdir(exist_ok=True)

    # Move items
    for item in items_to_move:
        dest = att_dir / item.name
        action = f"move {item.name} → {ATTACHMENTS}/{item.name}"
        report["actions"].append(action)
        if not dry_run:
            try:
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
            except Exception as exc:
                report["errors"].append(f"move {item.name}: {exc}")

    # Rewrite references in all .md files at root
    for md_file in paper_dir.glob("*.md"):
        try:
            original = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report["errors"].append(f"read {md_file.name}: {exc}")
            continue
        updated = rewrite_attachment_refs(original)
        if updated != original:
            report["actions"].append(f"rewrite-refs {md_file.name}")
            if not dry_run:
                try:
                    md_file.write_text(updated, encoding="utf-8")
                except OSError as exc:
                    report["errors"].append(f"write {md_file.name}: {exc}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Reorganize Obsidian paper folders.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without modifying files.")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    papers_root = base / "obsidian_vault" / "papers"

    if not papers_root.exists():
        print(f"papers directory not found: {papers_root}")
        return

    folders = sorted(
        d for d in papers_root.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
    )

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Reorganizing {len(folders)} paper folders ...\n")

    total_moved = 0
    total_rewritten = 0
    total_errors = 0

    for folder in folders:
        report = reorganize_folder(folder, dry_run=args.dry_run)
        moved = sum(1 for a in report["actions"] if a.startswith("move "))
        rewritten = sum(1 for a in report["actions"] if a.startswith("rewrite-refs"))
        errors = len(report["errors"])
        total_moved += moved
        total_rewritten += rewritten
        total_errors += errors

        if moved or rewritten or errors:
            status = f"  {folder.name}: {moved} moved, {rewritten} refs rewritten"
            if errors:
                status += f", {errors} ERRORS"
            print(status)
            for err in report["errors"]:
                print(f"    ERROR: {err}")

    print(f"\nDone. {total_moved} items moved, {total_rewritten} MDs rewritten, {total_errors} errors.")


if __name__ == "__main__":
    main()
