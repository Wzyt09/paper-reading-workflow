from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import fitz


MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
IMAGE_LINE_RE = re.compile(r"!\[[^\]]*\]\((assets/[^)]+)\)")
FIGURE_CAPTION_RE = re.compile(
    r"^(?:(?:FIG|Fig)\.?|Figure)\s*((?:S)?\d+)(?:\s*\([^)]+\))?[.:]?\s*(.*)",
    re.IGNORECASE,
)
FIG_LABEL_RE = re.compile(r"(?i)(?:fig(?:ure)?|\u56fe)\.?\s*(S?\d+)\b")
FIG_RANGE_RE = re.compile(
    r"(?i)(?:fig(?:ure)?|\u56fe)\.?\s*(S?)(\d+)\s*[-~\u2013\u2014\uFF0D]\s*(S?)(\d+)"
)
FULL_PAGE_SUMMARY_RE = re.compile(
    r"(?:^|/)(ref-\d+-page-(\d{3})\.(png|jpe?g|webp))$",
    re.IGNORECASE,
)
FIG_PAGE_FALLBACK_RE = re.compile(
    r"(?:^|/)pdf(\d+)-fig-([a-z0-9]+)-page-page-(\d{3})\.(png|jpe?g|webp)$",
    re.IGNORECASE,
)
PLAIN_PAGE_RE = re.compile(r"(?:^|/)pages/page-(\d{3})\.(png|jpe?g|webp)$", re.IGNORECASE)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MIN_PAGE_ASSET_BYTES = 10_000


@dataclass
class BadRef:
    kind: str
    page_number: int | None
    figure_label: str | None = None
    pdf_index: str = "1"


@dataclass
class PDFContext:
    index: str
    extracted_dir: Path | None
    packaged_pdf: Path | None
    packaged_figures: dict[str, list[Path]] = field(default_factory=dict)
    document_figures: dict[str, list[Path]] = field(default_factory=dict)
    page_assets: dict[int, list[Path]] = field(default_factory=dict)


@dataclass
class NoteContext:
    workspace_root: Path
    note_dir: Path
    note_md: Path
    attachments_dir: Path
    images_dir: Path
    manifest_path: Path | None
    pdf_contexts: dict[str, PDFContext]
    replacement_cache: dict[tuple[str, str, tuple[str, ...], int | None], str] = field(default_factory=dict)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair packaged paper-summary markdown image refs without touching the summary text."
    )
    parser.add_argument(
        "targets",
        nargs="*",
        type=Path,
        help="Markdown files, note directories, or roots to scan. Defaults to the Obsidian papers folder.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned fixes without writing files.",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-") or "item"


def strip_extended_path(path_str: str) -> str:
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[8:]
    if path_str.startswith("\\\\?\\"):
        return path_str[4:]
    return path_str


def extended_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    text = str(resolved)
    if os.name != "nt":
        return text
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def ext_path(path: Path) -> Path:
    return Path(extended_path(path))


def display_path(path: Path) -> str:
    return strip_extended_path(str(path))


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> tuple[str, str]:
    io_path = extended_path(path)
    with open(io_path, "rb") as handle:
        raw = handle.read()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8", errors="ignore"), newline


def write_text(path: Path, text: str, newline: str) -> None:
    io_path = extended_path(path)
    ensure_dir(path.parent)
    payload = text.replace("\n", newline)
    with open(io_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(payload)


def copy2_robust(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    shutil.copy2(extended_path(src), extended_path(dst))


def ensure_copy(src: Path, dst: Path) -> Path:
    ensure_dir(dst.parent)
    if not dst.exists():
        copy2_robust(src, dst)
    return dst


def is_external_ref(ref: str) -> bool:
    scheme = urlparse(ref).scheme.lower()
    return scheme in {"http", "https", "data", "mailto"}


def is_picture_text_line(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.startswith("**----- Start of picture text -----**")
        or stripped.startswith("**----- End of picture text -----**")
        or stripped == "<br>"
        or stripped.endswith("<br>")
    )


def normalize_figure_label(label: str) -> str:
    cleaned = label.strip().upper()
    if cleaned.startswith("S") and cleaned[1:].isdigit():
        return f"S{int(cleaned[1:])}"
    if cleaned.isdigit():
        return str(int(cleaned))
    return cleaned


def figure_sort_key(label: str) -> tuple[int, int]:
    normalized = normalize_figure_label(label)
    if normalized.startswith("S") and normalized[1:].isdigit():
        return (1, int(normalized[1:]))
    if normalized.isdigit():
        return (0, int(normalized))
    return (2, sys.maxsize)


def figure_token(label: str) -> str:
    normalized = normalize_figure_label(label)
    if normalized.isdigit():
        return f"{int(normalized):02d}"
    return slugify(normalized)


def figure_label_from_token(token: str) -> str:
    lowered = token.strip().lower()
    if lowered.startswith("s") and lowered[1:].isdigit():
        return f"S{int(lowered[1:])}"
    if lowered.isdigit():
        return str(int(lowered))
    return token.upper()


def ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def extract_figure_labels(text: str) -> list[str]:
    normalized = (
        text.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\uFF0D", "-")
        .replace("\uFF1A", ":")
    )
    labels: list[str] = []
    for match in FIG_RANGE_RE.finditer(normalized):
        left_prefix = match.group(1).upper()
        left_number = int(match.group(2))
        right_prefix = (match.group(3) or left_prefix).upper()
        right_number = int(match.group(4))
        if left_prefix != right_prefix or right_number < left_number:
            continue
        prefix = left_prefix
        for number in range(left_number, right_number + 1):
            labels.append(normalize_figure_label(f"{prefix}{number}" if prefix else str(number)))
    for match in FIG_LABEL_RE.finditer(normalized):
        labels.append(normalize_figure_label(match.group(1)))
    return ordered_unique(labels)


def parse_bad_ref(raw_ref: str) -> BadRef | None:
    ref = raw_ref.strip().strip("<>").replace("\\", "/")
    match = FIG_PAGE_FALLBACK_RE.search(ref)
    if match:
        return BadRef(
            kind="figure-page-fallback",
            pdf_index=str(int(match.group(1))),
            figure_label=figure_label_from_token(match.group(2)),
            page_number=int(match.group(3)),
        )
    match = FULL_PAGE_SUMMARY_RE.search(ref)
    if match:
        return BadRef(kind="summary-full-page", page_number=int(match.group(2)))
    match = PLAIN_PAGE_RE.search(ref)
    if match:
        return BadRef(kind="plain-page", page_number=int(match.group(1)))
    return None


def resolve_workspace_ref(note_md: Path, workspace_root: Path, raw_ref: str) -> Path | None:
    cleaned = unquote(raw_ref.strip().strip("<>").split("#", 1)[0])
    if not cleaned or is_external_ref(cleaned):
        return None
    candidate = Path(cleaned)
    if candidate.is_absolute():
        resolved = ext_path(candidate)
        return resolved if resolved.exists() else None
    note_relative = ext_path((note_md.parent / candidate).resolve())
    if note_relative.exists():
        return note_relative
    workspace_relative = ext_path((workspace_root / candidate).resolve())
    if workspace_relative.exists():
        return workspace_relative
    return None


def find_note_markdown(note_dir: Path) -> Path | None:
    matches = sorted(
        (
            child
            for child in note_dir.iterdir()
            if child.is_file() and child.suffix.lower() == ".md" and child.name != "manifest.json"
        ),
        key=lambda path: path.name,
    )
    return matches[0] if matches else None


def iter_note_dirs(targets: list[Path], workspace_root: Path) -> list[Path]:
    roots = targets or [workspace_root / "05-zotero_obsidian_sync" / "obsidian_vault" / "papers"]
    note_dirs: list[Path] = []
    seen: set[str] = set()
    for target in roots:
        resolved = ext_path(target)
        if resolved.is_file() and resolved.suffix.lower() == ".md":
            note_dir = resolved.parent
            key = str(note_dir)
            if key not in seen:
                seen.add(key)
                note_dirs.append(note_dir)
            continue
        if resolved.is_dir():
            manifest = resolved / "_attachments" / "manifest.json"
            if manifest.exists():
                key = str(resolved)
                if key not in seen:
                    seen.add(key)
                    note_dirs.append(resolved)
                continue
            for nested_manifest in resolved.rglob("manifest.json"):
                if nested_manifest.parent.name != "_attachments":
                    continue
                note_dir = nested_manifest.parent.parent
                key = str(note_dir)
                if key in seen:
                    continue
                seen.add(key)
                note_dirs.append(note_dir)
                continue
            for md in resolved.rglob("*.md"):
                if md.name == "document.md":
                    continue
                if "_attachments" in md.parts or "extracted" in md.parts:
                    continue
                key = str(md.parent)
                if key in seen:
                    continue
                seen.add(key)
                note_dirs.append(md.parent)
    return sorted(note_dirs, key=lambda path: display_path(path).lower())


def collect_figure_refs_before(lines: list[str], line_index: int) -> list[str]:
    refs: list[str] = []
    cursor = line_index - 1
    while cursor >= 0 and line_index - cursor <= 25:
        candidate = lines[cursor].strip()
        if FIGURE_CAPTION_RE.match(candidate):
            break
        image_match = IMAGE_LINE_RE.fullmatch(candidate)
        if image_match:
            refs.append(image_match.group(1))
        elif refs and candidate and not is_picture_text_line(candidate):
            break
        cursor -= 1
    refs.reverse()
    return refs


def collect_figure_refs_after(lines: list[str], line_index: int) -> list[str]:
    refs: list[str] = []
    content_lines_before_first_ref = 0
    cursor = line_index + 1
    while cursor < len(lines) and cursor - line_index <= 25:
        candidate = lines[cursor].strip()
        if FIGURE_CAPTION_RE.match(candidate):
            break
        image_match = IMAGE_LINE_RE.fullmatch(candidate)
        if image_match:
            refs.append(image_match.group(1))
        elif candidate and not is_picture_text_line(candidate):
            if refs:
                break
            content_lines_before_first_ref += 1
            if content_lines_before_first_ref > 2:
                break
        cursor += 1
    return refs


def collect_document_figures(extracted_dir: Path) -> dict[str, list[Path]]:
    document_md = extracted_dir / "document.md"
    if not document_md.is_file():
        return {}
    text, _ = read_text(document_md)
    lines = text.splitlines()
    figures: dict[str, list[Path]] = {}
    for line_index, line in enumerate(lines):
        match = FIGURE_CAPTION_RE.match(line.strip())
        if not match:
            continue
        label = normalize_figure_label(match.group(1))
        refs = collect_figure_refs_before(lines, line_index) or collect_figure_refs_after(lines, line_index)
        if not refs:
            continue
        bucket = figures.setdefault(label, [])
        for ref in refs:
            asset = extracted_dir / ref
            if asset.is_file():
                bucket.append(asset)
    for label, paths in list(figures.items()):
        unique_paths = ordered_unique([str(path) for path in paths])
        figures[label] = [Path(path) for path in unique_paths]
    return figures


def collect_page_assets(extracted_dir: Path) -> dict[int, list[Path]]:
    assets_dir = extracted_dir / "assets"
    if not assets_dir.is_dir():
        return {}
    page_assets: dict[int, list[Path]] = {}
    for asset in sorted(assets_dir.iterdir(), key=lambda path: path.name):
        if not asset.is_file() or asset.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if asset.stat().st_size < MIN_PAGE_ASSET_BYTES:
            continue
        match = re.search(r"\.pdf-(\d{4})-\d+\.", asset.name, re.IGNORECASE)
        if not match:
            continue
        page_number = int(match.group(1))
        page_assets.setdefault(page_number, []).append(asset)
    return page_assets


def build_pdf_contexts(note_dir: Path, attachments_dir: Path, manifest_data: dict[str, object] | None) -> dict[str, PDFContext]:
    if not manifest_data:
        return {}
    auto_materials = manifest_data.get("auto_materials", [])
    pdf_contexts: dict[str, PDFContext] = {}
    for entry in manifest_data.get("pdfs", []):
        index = str(entry.get("index", "1"))
        extracted_dir = attachments_dir / str(entry.get("extracted_dir", ""))
        packaged_pdf = attachments_dir / str(entry.get("packaged_pdf", ""))
        context = PDFContext(
            index=index,
            extracted_dir=extracted_dir if extracted_dir.is_dir() else None,
            packaged_pdf=packaged_pdf if packaged_pdf.is_file() else None,
        )
        for item in auto_materials:
            if item.get("kind") != "figure":
                continue
            packaged = str(item.get("packaged", ""))
            if not packaged.startswith(f"images/pdf{int(index):02d}-fig-"):
                continue
            figure_label = normalize_figure_label(str(item.get("figure", "")))
            figure_path = attachments_dir / packaged
            if figure_path.is_file():
                context.packaged_figures.setdefault(figure_label, []).append(figure_path)
        if context.extracted_dir is not None:
            context.document_figures = collect_document_figures(context.extracted_dir)
            context.page_assets = collect_page_assets(context.extracted_dir)
        for label, paths in list(context.packaged_figures.items()):
            unique_paths = ordered_unique([str(path) for path in paths])
            context.packaged_figures[label] = [Path(path) for path in unique_paths]
        pdf_contexts[index] = context
    return pdf_contexts


def build_note_context(note_dir: Path, workspace_root: Path) -> NoteContext | None:
    note_md = find_note_markdown(note_dir)
    if note_md is None:
        return None
    attachments_dir = note_dir / "_attachments"
    images_dir = attachments_dir / "images"
    manifest_path = attachments_dir / "manifest.json"
    manifest_data = None
    if manifest_path.is_file():
        manifest_data = json.loads(read_text(manifest_path)[0])
    return NoteContext(
        workspace_root=workspace_root,
        note_dir=note_dir,
        note_md=note_md,
        attachments_dir=attachments_dir,
        images_dir=images_dir,
        manifest_path=manifest_path if manifest_path.is_file() else None,
        pdf_contexts=build_pdf_contexts(note_dir, attachments_dir, manifest_data),
    )


def image_relative_ref(note_md: Path, image_path: Path) -> str:
    relative = os.path.relpath(display_path(image_path), display_path(note_md.parent))
    return relative.replace("\\", "/")


def normalized_ref(ref: str) -> str:
    return ref.strip().strip("<>").replace("\\", "/")


def unique_paths(paths: list[Path]) -> list[Path]:
    ordered = ordered_unique([str(path) for path in paths])
    return [Path(path) for path in ordered]


def combine_images(image_paths: list[Path], output_path: Path, *, dry_run: bool = False) -> Path:
    if output_path.exists() or dry_run:
        return output_path
    pixmaps = [fitz.Pixmap(extended_path(path)) for path in image_paths]
    width = max(pix.width for pix in pixmaps)
    gap = 24
    total_height = sum(pix.height for pix in pixmaps) + gap * max(0, len(pixmaps) - 1)
    pdf = fitz.open()
    page = pdf.new_page(width=width, height=total_height)
    y = 0.0
    for path, pix in zip(image_paths, pixmaps):
        x = (width - pix.width) / 2
        page.insert_image(fitz.Rect(x, y, x + pix.width, y + pix.height), filename=extended_path(path))
        y += pix.height + gap
    pix = page.get_pixmap(alpha=False)
    ensure_dir(output_path.parent)
    pix.save(extended_path(output_path))
    return output_path


def copy_document_images(
    paths: list[Path],
    images_dir: Path,
    prefix: str,
    *,
    dry_run: bool = False,
) -> list[Path]:
    copied: list[Path] = []
    for index, path in enumerate(paths, start=1):
        target = images_dir / f"{prefix}-panel-{index:02d}-{slugify(path.stem)}{path.suffix.lower()}"
        copied.append(target if dry_run else ensure_copy(path, target))
    return copied


def figure_crop_rect(page: fitz.Page, caption_rect: fitz.Rect) -> fitz.Rect | None:
    page_rect = page.rect
    column_threshold = page_rect.width / 2
    if caption_rect.x1 <= column_threshold + 24:
        x_limits = (0.0, column_threshold + 12)
    elif caption_rect.x0 >= column_threshold - 24:
        x_limits = (column_threshold - 12, page_rect.width)
    else:
        x_limits = (0.0, page_rect.width)

    candidates: list[fitz.Rect] = []
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            if rect.y1 > caption_rect.y0 + 12:
                continue
            if rect.x1 < x_limits[0] or rect.x0 > x_limits[1]:
                continue
            if rect.width < 8 or rect.height < 8:
                continue
            candidates.append(rect)

    for drawing in page.get_drawings():
        rect = drawing["rect"]
        area = rect.width * rect.height
        if area < 800 or area > page_rect.width * page_rect.height * 0.45:
            continue
        if rect.y1 > caption_rect.y0 + 12:
            continue
        if rect.x1 < x_limits[0] or rect.x0 > x_limits[1]:
            continue
        candidates.append(rect)

    if not candidates:
        top = max(0.0, caption_rect.y0 - 260)
        rect = fitz.Rect(x_limits[0], top, x_limits[1], max(top + 40, caption_rect.y0 - 8))
        return rect & page_rect

    x0 = min(rect.x0 for rect in candidates)
    y0 = min(rect.y0 for rect in candidates)
    x1 = max(rect.x1 for rect in candidates)
    y1 = max(rect.y1 for rect in candidates)
    rect = fitz.Rect(x0 - 12, y0 - 12, x1 + 12, y1 + 12)
    rect = rect & page_rect
    return rect if rect.width > 16 and rect.height > 16 else None


def search_caption_rect(page: fitz.Page, figure_label: str) -> fitz.Rect | None:
    tokens = [
        f"Figure {figure_label}",
        f"FIG. {figure_label}",
        f"Fig. {figure_label}",
    ]
    for token in tokens:
        hits = page.search_for(token)
        if hits:
            return hits[0]
    return None


def crop_figure_from_pdf(
    pdf_path: Path,
    page_number: int,
    figure_label: str,
    output_path: Path,
    *,
    dry_run: bool = False,
) -> Path | None:
    if output_path.exists() or dry_run:
        return output_path
    document = fitz.open(extended_path(pdf_path))
    page_index = page_number - 1
    if page_index < 0 or page_index >= document.page_count:
        return None
    page = document[page_index]
    caption_rect = search_caption_rect(page, figure_label)
    if caption_rect is None:
        return None
    clip = figure_crop_rect(page, caption_rect)
    if clip is None:
        return None
    pix = page.get_pixmap(clip=clip, dpi=220, alpha=False)
    ensure_dir(output_path.parent)
    pix.save(extended_path(output_path))
    return output_path


def merge_candidate_paths(
    paths: list[Path],
    note_md: Path,
    combined_output: Path,
    *,
    dry_run: bool = False,
) -> str:
    unique = unique_paths(paths)
    if not unique:
        raise ValueError("Expected at least one image path to merge.")
    if len(unique) == 1:
        return image_relative_ref(note_md, unique[0])
    combined = combine_images(unique, combined_output, dry_run=dry_run)
    return image_relative_ref(note_md, combined)


def repair_bad_ref(
    context: NoteContext,
    raw_ref: str,
    alt_text: str,
    context_text: str,
    *,
    dry_run: bool = False,
) -> str | None:
    bad_ref = parse_bad_ref(raw_ref)
    if bad_ref is None:
        return None
    alt_labels = extract_figure_labels(alt_text)
    context_labels = extract_figure_labels(context_text)
    labels = alt_labels if alt_labels else context_labels
    if bad_ref.figure_label:
        explicit = normalize_figure_label(bad_ref.figure_label)
        if explicit not in labels:
            labels = [explicit, *labels]
    labels = ordered_unique(labels)
    cache_key = (bad_ref.kind, raw_ref, tuple(labels), bad_ref.page_number)
    if cache_key in context.replacement_cache:
        return context.replacement_cache[cache_key]

    pdf_context = context.pdf_contexts.get(bad_ref.pdf_index) or next(iter(context.pdf_contexts.values()), None)
    if pdf_context is None:
        return None

    figure_paths: list[Path] = []
    for label in labels:
        if label in pdf_context.packaged_figures:
            figure_paths.extend(pdf_context.packaged_figures[label])
    if figure_paths:
        output_name = (
            f"repair-pdf{int(pdf_context.index):02d}-"
            f"{'-'.join(figure_token(label) for label in labels)}-combined.png"
        )
        repaired = merge_candidate_paths(
            figure_paths,
            context.note_md,
            context.images_dir / output_name,
            dry_run=dry_run,
        )
        context.replacement_cache[cache_key] = repaired
        return repaired

    copied_paths: list[Path] = []
    for label in labels:
        source_paths = pdf_context.document_figures.get(label, [])
        if not source_paths:
            continue
        prefix = f"repair-pdf{int(pdf_context.index):02d}-fig-{figure_token(label)}"
        copied_paths.extend(
            copy_document_images(source_paths, context.images_dir, prefix, dry_run=dry_run)
        )
    if copied_paths:
        output_name = (
            f"repair-pdf{int(pdf_context.index):02d}-"
            f"{'-'.join(figure_token(label) for label in labels)}-combined.png"
        )
        repaired = merge_candidate_paths(
            copied_paths,
            context.note_md,
            context.images_dir / output_name,
            dry_run=dry_run,
        )
        context.replacement_cache[cache_key] = repaired
        return repaired

    if bad_ref.page_number is not None:
        page_assets = pdf_context.page_assets.get(bad_ref.page_number, [])
        if page_assets:
            prefix = f"repair-pdf{int(pdf_context.index):02d}-page-{bad_ref.page_number:03d}"
            copied = copy_document_images(page_assets, context.images_dir, prefix, dry_run=dry_run)
            output_name = (
                f"repair-pdf{int(pdf_context.index):02d}-page-{bad_ref.page_number:03d}-combined.png"
            )
            repaired = merge_candidate_paths(
                copied,
                context.note_md,
                context.images_dir / output_name,
                dry_run=dry_run,
            )
            context.replacement_cache[cache_key] = repaired
            return repaired

    if bad_ref.page_number is not None and pdf_context.packaged_pdf is not None and labels:
        crops: list[Path] = []
        for label in labels:
            output_path = (
                context.images_dir
                / f"repair-pdf{int(pdf_context.index):02d}-page-{bad_ref.page_number:03d}-fig-{figure_token(label)}-crop.png"
            )
            cropped = crop_figure_from_pdf(
                pdf_context.packaged_pdf,
                bad_ref.page_number,
                label,
                output_path,
                dry_run=dry_run,
            )
            if cropped is not None:
                crops.append(cropped)
        if crops:
            output_name = (
                f"repair-pdf{int(pdf_context.index):02d}-"
                f"{'-'.join(figure_token(label) for label in labels)}-combined.png"
            )
            repaired = merge_candidate_paths(
                crops,
                context.note_md,
                context.images_dir / output_name,
                dry_run=dry_run,
            )
            context.replacement_cache[cache_key] = repaired
            return repaired

    return None


def repair_existing_ref(
    context: NoteContext,
    raw_ref: str,
    alt_text: str,
    context_text: str,
    *,
    dry_run: bool = False,
) -> str | None:
    cleaned = raw_ref.strip().strip("<>").replace("\\", "/")
    if "/_attachments/images/" not in f"/{cleaned}" and not cleaned.startswith("_attachments/images/"):
        return None
    if "/repair-" not in cleaned and "/pdf" not in cleaned:
        return None

    alt_labels = extract_figure_labels(alt_text)
    context_labels = extract_figure_labels(context_text)
    labels = alt_labels if alt_labels else context_labels
    labels = ordered_unique(labels)
    if not labels:
        return None

    pdf_match = re.search(r"pdf(\d+)", cleaned, re.IGNORECASE)
    pdf_index = str(int(pdf_match.group(1))) if pdf_match else "1"
    pdf_context = context.pdf_contexts.get(pdf_index) or next(iter(context.pdf_contexts.values()), None)
    if pdf_context is None:
        return None

    figure_paths: list[Path] = []
    for label in labels:
        if label in pdf_context.packaged_figures:
            figure_paths.extend(pdf_context.packaged_figures[label])
    if figure_paths:
        output_name = (
            f"repair-pdf{int(pdf_context.index):02d}-"
            f"{'-'.join(figure_token(label) for label in labels)}-combined.png"
        )
        return merge_candidate_paths(
            figure_paths,
            context.note_md,
            context.images_dir / output_name,
            dry_run=dry_run,
        )

    copied_paths: list[Path] = []
    for label in labels:
        source_paths = pdf_context.document_figures.get(label, [])
        if not source_paths:
            continue
        prefix = f"repair-pdf{int(pdf_context.index):02d}-fig-{figure_token(label)}"
        copied_paths.extend(
            copy_document_images(source_paths, context.images_dir, prefix, dry_run=dry_run)
        )
    if copied_paths:
        output_name = (
            f"repair-pdf{int(pdf_context.index):02d}-"
            f"{'-'.join(figure_token(label) for label in labels)}-combined.png"
        )
        return merge_candidate_paths(
            copied_paths,
            context.note_md,
            context.images_dir / output_name,
            dry_run=dry_run,
        )

    return None


def localize_workspace_image(
    context: NoteContext,
    raw_ref: str,
    *,
    dry_run: bool = False,
) -> str | None:
    source = resolve_workspace_ref(context.note_md, context.workspace_root, raw_ref)
    if source is None or source.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    target = context.images_dir / f"repair-local-{slugify(source.stem)}{source.suffix.lower()}"
    localized = target if dry_run else ensure_copy(source, target)
    return image_relative_ref(context.note_md, localized)


def repair_note(context: NoteContext, dry_run: bool) -> dict[str, int]:
    text, newline = read_text(context.note_md)
    parts: list[str] = []
    last_index = 0
    total_replacements = 0
    repaired_pages = 0
    localized_refs = 0
    unresolved = 0

    for match in MARKDOWN_IMAGE_RE.finditer(text):
        alt_text = match.group(1)
        raw_ref = match.group(2)
        replacement = None
        page_repair_applied = False

        prefix_lines = text[: match.start()].splitlines()[-12:]
        context_text = "\n".join(prefix_lines)

        bad_ref = parse_bad_ref(raw_ref)
        if bad_ref is not None:
            replacement = repair_bad_ref(
                context,
                raw_ref,
                alt_text,
                context_text,
                dry_run=dry_run,
            )
            if replacement is not None:
                page_repair_applied = True

        if replacement is None:
            replacement = repair_existing_ref(
                context,
                raw_ref,
                alt_text,
                context_text,
                dry_run=dry_run,
            )

        if replacement is None:
            cleaned = raw_ref.strip().strip("<>")
            if cleaned and not is_external_ref(cleaned):
                exists_in_note = resolve_workspace_ref(context.note_md, context.note_md.parent, cleaned) is not None
                if not cleaned.startswith("_attachments/") or not exists_in_note:
                    replacement = localize_workspace_image(context, raw_ref, dry_run=dry_run)
                    if replacement is not None:
                        localized_refs += 1

        if replacement is not None and normalized_ref(replacement) == normalized_ref(raw_ref):
            replacement = None
            page_repair_applied = False

        if replacement is None:
            if bad_ref is not None:
                unresolved += 1
            continue

        parts.append(text[last_index : match.start()])
        parts.append(f"![{alt_text}]({replacement})")
        last_index = match.end()
        total_replacements += 1
        if page_repair_applied:
            repaired_pages += 1

    if total_replacements == 0:
        return {
            "notes": 0,
            "replacements": 0,
            "repaired_pages": repaired_pages,
            "localized_refs": localized_refs,
            "unresolved": unresolved,
        }

    parts.append(text[last_index:])
    updated_text = "".join(parts)
    if not dry_run:
        write_text(context.note_md, updated_text, newline)

    return {
        "notes": 1,
        "replacements": total_replacements,
        "repaired_pages": repaired_pages,
        "localized_refs": localized_refs,
        "unresolved": unresolved,
    }


def main() -> int:
    args = parse_args()
    root = ext_path(repo_root())
    note_dirs = iter_note_dirs(args.targets, root)
    totals = {
        "notes": 0,
        "replacements": 0,
        "repaired_pages": 0,
        "localized_refs": 0,
        "unresolved": 0,
    }

    for note_dir in note_dirs:
        context = build_note_context(note_dir, root)
        if context is None:
            continue
        result = repair_note(context, dry_run=args.dry_run)
        if result["replacements"] == 0 and result["unresolved"] == 0:
            continue
        print(
            f"{display_path(context.note_md)}: "
            f"replacements={result['replacements']}, "
            f"page_repairs={result['repaired_pages']}, "
            f"localized={result['localized_refs']}, "
            f"unresolved={result['unresolved']}"
        )
        for key in totals:
            totals[key] += result[key]

    mode = "Dry-run" if args.dry_run else "Updated"
    print(
        f"{mode}: notes={totals['notes']}, replacements={totals['replacements']}, "
        f"page_repairs={totals['repaired_pages']}, localized={totals['localized_refs']}, "
        f"unresolved={totals['unresolved']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
