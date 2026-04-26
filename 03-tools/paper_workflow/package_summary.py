from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import fitz


IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
IMAGE_LINE_RE = re.compile(r"!\[[^\]]*\]\((assets/[^)]+)\)")
FIGURE_CAPTION_RE = re.compile(
    r"^(?:(?:FIG|Fig)\.?|Figure)\s*((?:S)?\d+)(?:\s*\([^)]+\))?\s*(?:[|.:])?\s*(.*)",
    re.IGNORECASE,
)
FIG_LABEL_RE = re.compile(r"(?i)(?:fig(?:ure)?|\u56fe)\.?\s*(S?\d+)\b")
FIG_RANGE_RE = re.compile(
    r"(?i)(?:fig(?:ure)?|\u56fe)\.?\s*(S?)(\d+)\s*[-~\u2013\u2014\uFF0D]\s*(S?)(\d+)"
)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
TOOLS_DIR_CANDIDATES = ("03-tools", "tools")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-") or "item"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package a paper summary markdown together with source PDFs, extracted materials, and referenced images."
    )
    parser.add_argument("summary_md", type=Path, help="Path to the summary markdown file.")
    parser.add_argument(
        "--pdf",
        dest="pdfs",
        type=Path,
        nargs="+",
        required=True,
        help="One or more source PDFs.",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        help="Output package directory. Defaults to <summary-md-stem>/ next to the markdown file.",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        help="Summary spec file used to produce the markdown. Defaults to 02-paper_summary_specs/default.md.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=170,
        help="DPI used by the PDF extractor for page renders. Default: 170.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the output package directory before writing.",
    )
    parser.add_argument(
        "--append-auto-materials",
        action="store_true",
        help="Append formulas and figures auto-extracted from the PDFs to the packaged markdown.",
    )
    return parser.parse_args()


def is_external_ref(ref: str) -> bool:
    scheme = urlparse(ref).scheme.lower()
    return scheme in {"http", "https", "data", "mailto"}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def make_writable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return
    path.chmod(mode | 0o200)


def remove_file_if_exists(path: Path) -> None:
    if not path.exists():
        return
    make_writable(path)
    path.unlink()


def _strip_extended_path(path_str: str) -> str:
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[8:]
    if path_str.startswith("\\\\?\\"):
        return path_str[4:]
    return path_str


def _rmtree_onerror(func, path_str, exc_info) -> None:
    path = Path(_strip_extended_path(path_str))
    if path.exists():
        make_writable(path)
    if getattr(func, "__name__", "") == "rmdir" and path.exists() and path.is_dir():
        for child in path.iterdir():
            if child.is_dir():
                shutil.rmtree(_extended_path(child) if os.name == "nt" else child, onerror=_rmtree_onerror)
            else:
                make_writable(child)
                child.unlink()
    func(path_str)


def _extended_path(path: Path) -> str:
    text = str(path.resolve())
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def copy2_robust(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    if os.name == "nt":
        shutil.copy2(_extended_path(src), _extended_path(dst))
        return
    shutil.copy2(src, dst)


def copytree_robust(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, onerror=_rmtree_onerror)
    if os.name == "nt":
        shutil.copytree(_extended_path(src), _extended_path(dst))
        return
    shutil.copytree(src, dst)


def copy_unique(src: Path, dest_dir: Path, dest_name: str) -> Path:
    ensure_dir(dest_dir)
    candidate = dest_dir / dest_name
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while candidate.exists():
        candidate = dest_dir / f"{stem}-{index}{suffix}"
        index += 1
    copy2_robust(src, candidate)
    return candidate


def resolve_local_ref(summary_path: Path, ref: str) -> Path | None:
    cleaned = ref.strip().strip("<>").split("#", 1)[0]
    cleaned = unquote(cleaned)
    if not cleaned or is_external_ref(cleaned):
        return None
    candidate = Path(cleaned)
    if not candidate.is_absolute():
        candidate = (summary_path.parent / candidate).resolve()
    return candidate


def resolve_local_ref_with_search_roots(
    summary_path: Path,
    ref: str,
    search_roots: list[Path] | None = None,
) -> Path | None:
    candidate = resolve_local_ref(summary_path, ref)
    if candidate is not None and candidate.exists():
        return candidate

    cleaned = ref.strip().strip("<>").split("#", 1)[0]
    cleaned = unquote(cleaned)
    if not cleaned or is_external_ref(cleaned):
        return candidate

    for root in search_roots or []:
        try:
            resolved = (root / cleaned).resolve()
        except OSError:
            continue
        if resolved.exists():
            return resolved

        basename = Path(cleaned).name
        if basename:
            try:
                matches = list(root.rglob(basename))
            except OSError:
                matches = []
            if matches:
                return matches[0].resolve()

            if ".pdf-" in basename:
                suffix = basename.split(".pdf-", 1)[1]
                pattern = f"*.pdf-{suffix}"
                try:
                    suffix_matches = list(root.rglob(pattern))
                except OSError:
                    suffix_matches = []
                if suffix_matches:
                    return suffix_matches[0].resolve()
    return candidate


def normalize_caption_line(line: str) -> str:
    stripped = line.strip()
    stripped = re.sub(r"^[>*_\s`|]+", "", stripped)
    stripped = re.sub(r"[*_`\s]+$", "", stripped)
    return stripped.strip()


def match_figure_caption(line: str) -> re.Match[str] | None:
    return FIGURE_CAPTION_RE.match(normalize_caption_line(line))


def ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_figure_label(label: str) -> str:
    value = label.strip().upper().replace(" ", "")
    if value.startswith("S") and value[1:].isdigit():
        return f"S{int(value[1:])}"
    if value.isdigit():
        return str(int(value))
    return value


def extract_figure_labels(text: str) -> list[str]:
    labels: list[str] = []
    for match in FIG_RANGE_RE.finditer(text):
        prefix1, start_raw, prefix2, end_raw = match.groups()
        if prefix1.upper() != prefix2.upper():
            continue
        start = int(start_raw)
        end = int(end_raw)
        if end < start or end - start > 12:
            continue
        prefix = prefix1.upper()
        for number in range(start, end + 1):
            labels.append(normalize_figure_label(f"{prefix}{number}" if prefix else str(number)))
    for match in FIG_LABEL_RE.finditer(text):
        labels.append(normalize_figure_label(match.group(1)))
    return ordered_unique(labels)


def image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with fitz.open(str(path)) as image_doc:
            if image_doc.page_count < 1:
                return None
            rect = image_doc[0].rect
            return int(round(rect.width)), int(round(rect.height))
    except Exception:
        return None


def representative_page_dimensions(extracted_dir: Path) -> tuple[int, int] | None:
    pages_dir = extracted_dir / "pages"
    if not pages_dir.is_dir():
        return None
    for candidate in sorted(pages_dir.glob("page-*.png")):
        size = image_dimensions(candidate)
        if size is not None:
            return size
    return None


def looks_like_full_page_asset(asset_path: Path, page_dimensions_hint: tuple[int, int] | None) -> bool:
    if page_dimensions_hint is None:
        return False
    asset_size = image_dimensions(asset_path)
    if asset_size is None:
        return False
    page_width, page_height = page_dimensions_hint
    asset_width, asset_height = asset_size
    if page_width <= 0 or page_height <= 0:
        return False
    width_ratio = asset_width / page_width
    height_ratio = asset_height / page_height
    asset_ratio = asset_width / max(asset_height, 1)
    page_ratio = page_width / max(page_height, 1)
    area_ratio = (asset_width * asset_height) / max(page_width * page_height, 1)
    return (
        width_ratio >= 0.82
        and height_ratio >= 0.82
        and abs(asset_ratio - page_ratio) <= 0.14
        and area_ratio >= 0.72
    )


def is_probable_figure_asset(asset_path: Path, page_dimensions_hint: tuple[int, int] | None) -> bool:
    asset_size = image_dimensions(asset_path)
    if asset_size is None:
        return False
    width, height = asset_size
    if width < 140 or height < 90:
        return False
    if page_dimensions_hint is not None:
        page_width, page_height = page_dimensions_hint
        area_ratio = (width * height) / max(page_width * page_height, 1)
        if area_ratio < 0.015:
            return False
    return not looks_like_full_page_asset(asset_path, page_dimensions_hint)


def search_caption_rect_in_document(pdf_path: Path, figure_label: str) -> tuple[int, fitz.Rect] | None:
    document = fitz.open(str(pdf_path))
    try:
        tokens = [
            f"Figure {figure_label}",
            f"FIG. {figure_label}",
            f"Fig. {figure_label}",
            f"Fig {figure_label}",
        ]
        for page_index, page in enumerate(document):
            for token in tokens:
                hits = page.search_for(token)
                if hits:
                    return page_index, hits[0]
    finally:
        document.close()
    return None


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

    if candidates:
        x0 = min(rect.x0 for rect in candidates)
        y0 = min(rect.y0 for rect in candidates)
        x1 = max(rect.x1 for rect in candidates)
        y1 = max(rect.y1 for rect in candidates)
        if 0 <= caption_rect.y0 - y1 <= 48:
            y1 = max(y1, caption_rect.y1 + 8)
        rect = fitz.Rect(x0 - 12, y0 - 12, x1 + 12, y1 + 12)
        rect = rect & page_rect
        return rect if rect.width > 16 and rect.height > 16 else None

    top = max(0.0, caption_rect.y0 - min(420, page_rect.height * 0.45))
    rect = fitz.Rect(x_limits[0], top, x_limits[1], max(top + 40, caption_rect.y0 - 8))
    rect = rect & page_rect
    return rect if rect.width > 16 and rect.height > 16 else None


def crop_figure_from_pdf(pdf_path: Path, figure_label: str, output_path: Path) -> Path | None:
    if output_path.exists():
        return output_path
    document = fitz.open(str(pdf_path))
    try:
        search_result = None
        tokens = [
            f"Figure {figure_label}",
            f"FIG. {figure_label}",
            f"Fig. {figure_label}",
            f"Fig {figure_label}",
        ]
        for page_index, page in enumerate(document):
            for token in tokens:
                hits = page.search_for(token)
                if hits:
                    search_result = (page_index, hits[0])
                    break
            if search_result is not None:
                break
        if search_result is None:
            return None
        page_index, caption_rect = search_result
        page = document[page_index]
        clip = figure_crop_rect(page, caption_rect)
        if clip is None:
            return None
        ensure_dir(output_path.parent)
        page.get_pixmap(clip=clip, dpi=220, alpha=False).save(output_path)
        return output_path
    finally:
        document.close()


def rewrite_summary_images(
    summary_text: str,
    summary_path: Path,
    images_dir: Path,
    *,
    search_roots: list[Path] | None = None,
    source_pdfs_by_root: dict[Path, Path] | None = None,
) -> tuple[str, list[dict[str, str]], list[str]]:
    copied_images: list[dict[str, str]] = []
    missing_refs: list[str] = []
    mapping: dict[Path, str] = {}
    crop_mapping: dict[tuple[Path, str], Path] = {}
    image_index = 0
    search_roots = search_roots or []
    extracted_to_pdf: list[tuple[Path, Path]] = []
    explicit_sources = source_pdfs_by_root or {}
    for root, source_pdf in explicit_sources.items():
        try:
            extracted_to_pdf.append((root.resolve(), source_pdf.resolve()))
        except OSError:
            continue
    for root in search_roots:
        if any(existing_root == root.resolve() for existing_root, _ in extracted_to_pdf):
            continue
        metadata_path = root / "metadata.json"
        if not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        source_pdf_raw = str(metadata.get("source_pdf") or "").strip()
        if not source_pdf_raw:
            continue
        source_pdf = Path(source_pdf_raw).expanduser().resolve()
        extracted_to_pdf.append((root.resolve(), source_pdf))

    page_dimensions_cache: dict[Path, tuple[int, int] | None] = {}

    def page_dimensions_for_resolved(resolved: Path) -> tuple[int, int] | None:
        for root in search_roots:
            try:
                relative = resolved.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            if not relative.parts:
                continue
            if relative.parts[0] != "assets":
                continue
            if root not in page_dimensions_cache:
                page_dimensions_cache[root] = representative_page_dimensions(root)
            return page_dimensions_cache[root]
        return None

    def repair_with_crop(raw_ref: str, context_text: str) -> Path | None:
        labels = extract_figure_labels(context_text)
        if not labels:
            return None
        for label in labels:
            for extracted_root, source_pdf in extracted_to_pdf:
                if not source_pdf.is_file():
                    continue
                cache_key = (source_pdf, label)
                if cache_key in crop_mapping and crop_mapping[cache_key].exists():
                    return crop_mapping[cache_key]
                output = images_dir / f"summary-{slugify(label)}-crop.png"
                cropped = crop_figure_from_pdf(source_pdf, label, output)
                if cropped is not None:
                    crop_mapping[cache_key] = cropped
                    copied_images.append(
                        {
                            "source": str(source_pdf),
                            "packaged": f"images/{cropped.name}",
                            "repair": f"crop-figure-{label}",
                        }
                    )
                    return cropped
        return None

    lines = summary_text.splitlines()
    rewritten_lines: list[str] = []

    for line_index, line in enumerate(lines):
        context_text = "\n".join(lines[max(0, line_index - 2) : min(len(lines), line_index + 3)])

        def replace(match: re.Match[str]) -> str:
            nonlocal image_index
            alt_text = match.group(1)
            raw_ref = match.group(2)
            resolved = resolve_local_ref_with_search_roots(summary_path, raw_ref, search_roots)
            repaired_crop: Path | None = None

            if resolved is None or not resolved.exists():
                repaired_crop = repair_with_crop(raw_ref, f"{alt_text}\n{context_text}")
                if repaired_crop is None:
                    missing_refs.append(raw_ref)
                    return match.group(0)
                return f"![{alt_text}](images/{repaired_crop.name})"

            if resolved in mapping:
                return f"![{alt_text}](images/{mapping[resolved]})"

            page_dimensions_hint = page_dimensions_for_resolved(resolved)
            if looks_like_full_page_asset(resolved, page_dimensions_hint):
                repaired_crop = repair_with_crop(raw_ref, f"{alt_text}\n{context_text}")
                if repaired_crop is not None:
                    return f"![{alt_text}](images/{repaired_crop.name})"

            image_index += 1
            dest_name = f"ref-{image_index:02d}-{slugify(resolved.stem)}{resolved.suffix.lower()}"
            copied = copy_unique(resolved, images_dir, dest_name)
            mapping[resolved] = copied.name
            copied_images.append(
                {
                    "source": str(resolved),
                    "packaged": f"images/{copied.name}",
                }
            )
            return f"![{alt_text}](images/{mapping[resolved]})"

        rewritten_lines.append(IMAGE_REF_RE.sub(replace, line))

    rewritten = "\n".join(rewritten_lines)
    if summary_text.endswith("\n"):
        rewritten += "\n"
    return rewritten, copied_images, missing_refs


def package_root(summary_path: Path, outdir: Path | None) -> Path:
    if outdir:
        return outdir.expanduser().resolve()
    return (summary_path.parent / summary_path.stem).resolve()


def find_repo_root(summary_path: Path) -> Path:
    current = summary_path.parent.resolve()
    for candidate in [current, *current.parents]:
        for tools_dirname in TOOLS_DIR_CANDIDATES:
            if (candidate / tools_dirname / "pdf_tools" / "extract_pdf.py").is_file():
                return candidate
    raise FileNotFoundError(
        "Could not find 03-tools/pdf_tools/extract_pdf.py from the summary location."
    )


def find_tools_dir(repo_root: Path) -> Path:
    for tools_dirname in TOOLS_DIR_CANDIDATES:
        candidate = repo_root / tools_dirname
        if (candidate / "pdf_tools" / "extract_pdf.py").is_file():
            return candidate
    raise FileNotFoundError(f"Could not find a supported tools directory under: {repo_root}")


def copy_and_extract_pdfs(
    pdfs: list[Path],
    package_dir: Path,
    repo_root: Path,
    dpi: int,
) -> list[dict[str, str]]:
    tools_root = find_tools_dir(repo_root)
    sources_dir = package_dir / "sources"
    extracted_root = package_dir / "extracted"
    temp_root = tools_root / "paper_workflow" / ".tmp_extract" / uuid.uuid4().hex
    ensure_dir(sources_dir)
    ensure_dir(extracted_root)
    ensure_dir(temp_root)

    extractor = tools_root / "pdf_tools" / "extract_pdf.py"
    venv_python = tools_root / "pdf_tools" / ".venv" / "Scripts" / "python.exe"
    python_exe = venv_python if venv_python.is_file() else Path(sys.executable)

    packaged: list[dict[str, str]] = []
    for index, pdf in enumerate(pdfs, start=1):
        resolved_pdf = pdf.expanduser().resolve()
        if not resolved_pdf.is_file():
            raise FileNotFoundError(f"PDF not found: {resolved_pdf}")
        slug = slugify(resolved_pdf.stem)
        copied_pdf = copy_unique(
            resolved_pdf,
            sources_dir,
            f"{index:02d}-{slug}{resolved_pdf.suffix.lower()}",
        )
        extracted_dir = extracted_root / f"{index:02d}-{slug}"
        if extracted_dir.exists():
            shutil.rmtree(extracted_dir, onerror=_rmtree_onerror)
        temp_pdf = temp_root / f"p{index:02d}{resolved_pdf.suffix.lower()}"
        temp_out = temp_root / f"e{index:02d}"
        copy2_robust(resolved_pdf, temp_pdf)
        cmd = [
            str(python_exe),
            str(extractor),
            str(temp_pdf),
            "--outdir",
            str(temp_out),
            "--dpi",
            str(dpi),
            "--clean",
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        subprocess.run(cmd, check=True, env=env)
        copytree_robust(temp_out, extracted_dir)
        if temp_pdf.exists():
            remove_file_if_exists(temp_pdf)
        if temp_out.exists():
            shutil.rmtree(temp_out, onerror=_rmtree_onerror)
        packaged.append(
            {
                "index": str(index),
                "source_pdf": str(resolved_pdf),
                "packaged_pdf": f"sources/{copied_pdf.name}",
                "extracted_dir": f"extracted/{extracted_dir.name}",
                "slug": slug,
            }
        )
    if temp_root.exists():
        shutil.rmtree(temp_root, ignore_errors=True)
    return packaged


def clean_text(text: str) -> str:
    text = re.sub(r"[_*`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def section_headings(summary_text: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    for line_no, line in enumerate(summary_text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("## "):
            headings.append((line_no, stripped[3:].strip()))
    return headings


def is_probable_math_code_span(code_span: str) -> bool:
    stripped = code_span.strip()
    if not stripped:
        return False

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        return False
    if stripped.startswith("accepted="):
        return False
    if stripped.startswith("arXiv:"):
        return False
    if re.fullmatch(r"10\.\d{4,9}/\S+", stripped):
        return False
    if re.search(r"\.(pdf|tex|md|json|png|jpg|jpeg|svg|gif)$", stripped, re.IGNORECASE):
        return False
    if stripped in {"CZ", "QND", "EMCCD", "Rb", "Cs", "Z", "SM.2", "SM.3"}:
        return False

    if (
        "/" in stripped
        and "\\" not in stripped
        and not any(marker in stripped for marker in ("_", "^", "{", "}", "=", "|", "<", ">"))
        and not re.search(r"\bO\(", stripped)
    ):
        return False

    if any(marker in stripped for marker in ("\\", "_", "^", "{", "}", "|", "⟨", "⟩", "≡", "±")):
        return True
    if "=" in stripped and re.search(r"[A-Za-z]", stripped):
        return True
    if re.search(r"\bO\([^)]*\)", stripped):
        return True
    if re.search(r"[A-Za-z].*/[A-Za-z]", stripped):
        return True
    return False


def suspicious_inline_math(summary_text: str) -> list[dict[str, str | int]]:
    issues: list[dict[str, str | int]] = []
    for line_no, line in enumerate(summary_text.splitlines(), start=1):
        for match in INLINE_CODE_RE.finditer(line):
            code_span = match.group(1)
            if is_probable_math_code_span(code_span):
                issues.append({"line": line_no, "text": code_span})
    return issues


def resolve_summary_spec(repo_root: Path, explicit_spec: Path | None) -> Path:
    if explicit_spec is not None:
        spec_path = explicit_spec.expanduser().resolve()
        if not spec_path.is_file():
            raise FileNotFoundError(f"Summary spec not found: {spec_path}")
        return spec_path
    return (repo_root / "02-paper_summary_specs" / "default.md").resolve()


def run_summary_checks(
    summary_text: str,
    spec_path: Path,
) -> dict[str, object]:
    spec_text = ""
    if spec_path.is_file():
        spec_text = spec_path.read_text(encoding="utf-8")

    checks: dict[str, object] = {
        "spec_source": str(spec_path),
        "errors": [],
        "warnings": [],
    }

    headings = section_headings(summary_text)
    heading_names = [name for _, name in headings]

    abstract_translation_required = "摘要翻译" in spec_text and "一句话结论" in spec_text
    if abstract_translation_required:
        if "一句话结论" not in heading_names:
            checks["errors"].append("Missing required section: ## 一句话结论")
        if "摘要翻译" not in heading_names:
            checks["errors"].append("Missing required section: ## 摘要翻译")
        if "一句话结论" in heading_names and "摘要翻译" in heading_names:
            conclusion_index = heading_names.index("一句话结论")
            abstract_index = heading_names.index("摘要翻译")
            if abstract_index != conclusion_index + 1:
                checks["errors"].append("`## 摘要翻译` must appear immediately after `## 一句话结论`.")

    enforce_inline_math = (
        ("行内公式" in spec_text and "$...$" in spec_text)
        or ("不要用反引号" in spec_text and "公式" in spec_text)
    )
    if enforce_inline_math:
        inline_math_issues = suspicious_inline_math(summary_text)
        if inline_math_issues:
            preview = ", ".join(
                f"line {issue['line']}: `{issue['text']}`"
                for issue in inline_math_issues[:10]
            )
            remaining = len(inline_math_issues) - 10
            suffix = f" (+{remaining} more)" if remaining > 0 else ""
            checks["errors"].append(
                "Inline math must use $...$ instead of backticks. "
                f"Found suspicious spans: {preview}{suffix}"
            )
            checks["inline_math_issues"] = inline_math_issues

    return checks


def page_number_from_ref(ref: str) -> int | None:
    match = re.search(r"-(\d{4})-\d+\.[A-Za-z0-9]+$", ref)
    if not match:
        return None
    return int(match.group(1))


def is_picture_text_line(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.startswith("**----- Start of picture text -----**")
        or stripped.startswith("**----- End of picture text -----**")
        or stripped == "<br>"
        or stripped.endswith("<br>")
    )


def figure_sort_key(label: str) -> tuple[int, int]:
    normalized = label.strip().upper()
    if normalized.startswith("S") and normalized[1:].isdigit():
        return (1, int(normalized[1:]))
    if normalized.isdigit():
        return (0, int(normalized))
    return (2, sys.maxsize)


def figure_token(label: str) -> str:
    normalized = label.strip()
    if normalized.isdigit():
        return f"{int(normalized):02d}"
    return slugify(normalized)


def collect_figure_refs_before(lines: list[str], line_index: int) -> list[str]:
    refs: list[str] = []
    cursor = line_index - 1
    while cursor >= 0 and line_index - cursor <= 25:
        candidate = lines[cursor].strip()
        if match_figure_caption(candidate):
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
        if match_figure_caption(candidate):
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


def collect_auto_materials(
    extracted_dir: Path,
    images_dir: Path,
    pdf_index: int,
    source_pdf: Path | None = None,
) -> tuple[str, list[dict[str, str]]]:
    document_md = extracted_dir / "document.md"
    assets_dir = extracted_dir / "assets"
    if not document_md.is_file() or not assets_dir.is_dir():
        return "", []

    lines = document_md.read_text(encoding="utf-8").splitlines()
    page_dimensions_hint = representative_page_dimensions(extracted_dir)
    asset_occurrences: list[tuple[int, str]] = []
    asset_sizes: dict[str, int] = {}
    for line_index, line in enumerate(lines):
        match = IMAGE_LINE_RE.search(line.strip())
        if not match:
            continue
        ref = match.group(1)
        asset_path = extracted_dir / ref
        if asset_path.is_file():
            asset_occurrences.append((line_index, ref))
            asset_sizes[ref] = asset_path.stat().st_size

    used_refs: set[str] = set()
    near_figure_refs: set[str] = set()
    records: list[dict[str, str]] = []
    appendix_parts: list[str] = []
    formula_counter = 0

    figure_groups: list[dict[str, object]] = []
    for line_index, line in enumerate(lines):
        caption_match = match_figure_caption(line.strip())
        if not caption_match:
            continue

        figure_label = normalize_figure_label(caption_match.group(1))
        caption_text = clean_text(normalize_caption_line(line))
        refs_before_caption = collect_figure_refs_before(lines, line_index)
        refs_after_caption = collect_figure_refs_after(lines, line_index)
        refs_near_caption = refs_before_caption or refs_after_caption
        near_figure_refs.update(refs_before_caption)
        near_figure_refs.update(refs_after_caption)
        usable_refs = [
            ref
            for ref in refs_near_caption
            if ref not in used_refs
            and asset_sizes.get(ref, 0) >= 3000
            and is_probable_figure_asset(extracted_dir / ref, page_dimensions_hint)
        ]

        if usable_refs:
            figure_groups.append(
                {
                    "figure_label": figure_label,
                    "caption": caption_text,
                    "refs": usable_refs,
                }
            )
            used_refs.update(usable_refs)
            continue

        if source_pdf is not None and source_pdf.is_file():
            cropped_output = images_dir / f"pdf{pdf_index:02d}-fig-{figure_token(figure_label)}-crop.png"
            cropped = crop_figure_from_pdf(source_pdf, figure_label, cropped_output)
            if cropped is not None:
                figure_groups.append(
                    {
                        "figure_label": figure_label,
                        "caption": caption_text,
                        "cropped": cropped,
                    }
                )
                records.append(
                    {
                        "kind": "figure",
                        "figure": figure_label,
                        "source": str(source_pdf),
                        "packaged": f"images/{cropped.name}",
                        "repair": "crop-from-pdf",
                    }
                )

    figure_groups.sort(key=lambda group: figure_sort_key(str(group["figure_label"])))

    formula_refs = [
        ref
        for _, ref in asset_occurrences
        if ref not in used_refs and ref not in near_figure_refs and asset_sizes.get(ref, 0) >= 2500
    ]

    if formula_refs:
        appendix_parts.append("#### 自动提取公式\n")
        for ref in formula_refs:
            formula_counter += 1
            src = extracted_dir / ref
            copied = copy_unique(
                src,
                images_dir,
                f"pdf{pdf_index:02d}-formula-{formula_counter:02d}-{slugify(src.stem)}{src.suffix.lower()}",
            )
            appendix_parts.append(f"![公式 {formula_counter}](images/{copied.name})\n")
            records.append(
                {
                    "kind": "formula",
                    "source": str(src),
                    "packaged": f"images/{copied.name}",
                }
            )
        appendix_parts.append("")

    if figure_groups:
        appendix_parts.append("#### 自动提取图片\n")
        for group in figure_groups:
            figure_label = str(group["figure_label"])
            appendix_parts.append(f"##### Fig. {figure_label}\n")
            appendix_parts.append(group["caption"])
            appendix_parts.append("")

            if "cropped" in group:
                cropped = Path(str(group["cropped"]))
                appendix_parts.append(f"![Fig. {figure_label}](images/{cropped.name})\n")
            elif "refs" in group:
                refs = group["refs"]
                for panel_index, ref in enumerate(refs, start=1):
                    src = extracted_dir / ref
                    token = figure_token(figure_label)
                    copied = copy_unique(
                        src,
                        images_dir,
                        f"pdf{pdf_index:02d}-fig-{token}-panel-{panel_index:02d}-{slugify(src.stem)}{src.suffix.lower()}",
                    )
                    appendix_parts.append(
                        f"![Fig. {figure_label} - Panel {panel_index}](images/{copied.name})\n"
                    )
                    records.append(
                        {
                            "kind": "figure",
                            "figure": figure_label,
                            "source": str(src),
                            "packaged": f"images/{copied.name}",
                        }
                    )

            appendix_parts.append("")

    if not appendix_parts:
        return "", records

    header = [
        f"### 来源 PDF {pdf_index}\n",
        f"- 自动提取 Markdown: [{document_md.name}](extracted/{extracted_dir.name}/{document_md.name})",
        f"- 自动提取纯文本: [document.txt](extracted/{extracted_dir.name}/document.txt)",
        f"- 提取元数据: [metadata.json](extracted/{extracted_dir.name}/metadata.json)",
        "",
    ]
    return "\n".join(header + appendix_parts).rstrip() + "\n", records


def write_manifest(
    package_dir: Path,
    summary_source: Path,
    packaged_summary_name: str,
    packaged_pdfs: list[dict[str, str]],
    copied_summary_images: list[dict[str, str]],
    auto_material_records: list[dict[str, str]],
    missing_summary_refs: list[str],
    appended_auto_materials: bool,
    summary_checks: dict[str, object],
) -> None:
    manifest = {
        "summary_source": str(summary_source),
        "packaged_summary": packaged_summary_name,
        "append_auto_materials": appended_auto_materials,
        "pdfs": packaged_pdfs,
        "summary_images": copied_summary_images,
        "auto_materials": auto_material_records,
        "missing_summary_image_refs": missing_summary_refs,
        "summary_checks": summary_checks,
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    summary_path = args.summary_md.expanduser().resolve()
    if not summary_path.is_file():
        raise FileNotFoundError(f"Summary markdown not found: {summary_path}")

    repo_root = find_repo_root(summary_path)
    spec_path = resolve_summary_spec(repo_root, args.spec)
    summary_text = summary_path.read_text(encoding="utf-8")
    summary_checks = run_summary_checks(summary_text, spec_path)
    if summary_checks["errors"]:
        formatted_errors = "\n".join(f"- {error}" for error in summary_checks["errors"])
        raise ValueError(f"Summary validation failed for {summary_path}:\n{formatted_errors}")

    package_dir = package_root(summary_path, args.outdir)
    if args.clean and package_dir.exists():
        last_cleanup_error: Exception | None = None
        cleanup_target = _extended_path(package_dir) if os.name == "nt" else str(package_dir)
        for _ in range(5):
            try:
                shutil.rmtree(cleanup_target, onerror=_rmtree_onerror)
                last_cleanup_error = None
                break
            except OSError as exc:
                last_cleanup_error = exc
                time.sleep(0.4)
        if package_dir.exists() and last_cleanup_error is not None:
            raise last_cleanup_error
        for _ in range(20):
            if not package_dir.exists():
                break
            time.sleep(0.2)
    ensure_dir(package_dir)

    images_dir = package_dir / "images"
    ensure_dir(images_dir)

    packaged_pdfs = copy_and_extract_pdfs(args.pdfs, package_dir, repo_root, args.dpi)

    summary_search_roots = [package_dir / pdf_info["extracted_dir"] for pdf_info in packaged_pdfs]
    summary_pdf_sources = {
        (package_dir / pdf_info["extracted_dir"]).resolve(): Path(str(pdf_info["source_pdf"])).resolve()
        for pdf_info in packaged_pdfs
    }
    rewritten_summary, copied_summary_images, missing_summary_refs = rewrite_summary_images(
        summary_text,
        summary_path,
        images_dir,
        search_roots=summary_search_roots,
        source_pdfs_by_root=summary_pdf_sources,
    )

    page_dimensions_cache: dict[Path, tuple[int, int] | None] = {}
    full_page_summary_sources: list[str] = []
    for item in copied_summary_images:
        source_text = str(item.get("source") or "").strip()
        if not source_text:
            continue
        source_path = Path(source_text)
        if not source_path.is_file():
            continue
        for root in summary_search_roots:
            try:
                relative = source_path.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            if not relative.parts or relative.parts[0] != "assets":
                continue
            if root not in page_dimensions_cache:
                page_dimensions_cache[root] = representative_page_dimensions(root)
            if looks_like_full_page_asset(source_path, page_dimensions_cache[root]):
                full_page_summary_sources.append(source_text)
            break

    summary_image_errors: list[str] = []
    if missing_summary_refs:
        preview = ", ".join(sorted(dict.fromkeys(missing_summary_refs))[:10])
        summary_image_errors.append(
            f"Summary contains unresolved image refs after packaging rewrite: {preview}"
        )
    if full_page_summary_sources:
        preview = ", ".join(sorted(dict.fromkeys(full_page_summary_sources))[:10])
        summary_image_errors.append(
            "Summary still references full-page PDF screenshots after image rewrite: "
            f"{preview}"
        )
    if summary_image_errors:
        raise ValueError("Summary image validation failed:\n- " + "\n- ".join(summary_image_errors))

    auto_material_records: list[dict[str, str]] = []
    if args.append_auto_materials:
        appendix_chunks: list[str] = []
        for pdf_info in packaged_pdfs:
            extracted_dir = package_dir / pdf_info["extracted_dir"]
            chunk, records = collect_auto_materials(
                extracted_dir=extracted_dir,
                images_dir=images_dir,
                pdf_index=int(pdf_info["index"]),
                source_pdf=Path(str(pdf_info["source_pdf"])),
            )
            if chunk:
                appendix_chunks.append(chunk)
            auto_material_records.extend(records)

        if appendix_chunks:
            rewritten_summary = (
                rewritten_summary.rstrip()
                + "\n\n## 自动提取的原文公式与图片\n\n"
                + "\n".join(chunk.rstrip() for chunk in appendix_chunks)
                + "\n"
            )

    packaged_summary_name = f"{summary_path.stem}.md"
    (package_dir / packaged_summary_name).write_text(rewritten_summary, encoding="utf-8")

    write_manifest(
        package_dir=package_dir,
        summary_source=summary_path,
        packaged_summary_name=packaged_summary_name,
        packaged_pdfs=packaged_pdfs,
        copied_summary_images=copied_summary_images,
        auto_material_records=auto_material_records,
        missing_summary_refs=missing_summary_refs,
        appended_auto_materials=args.append_auto_materials,
        summary_checks=summary_checks,
    )

    print(
        json.dumps(
            {
                "package_dir": str(package_dir),
                "summary": str(package_dir / packaged_summary_name),
                "pdf_count": len(packaged_pdfs),
                "summary_image_count": len(copied_summary_images),
                "auto_material_count": len(auto_material_records),
                "missing_summary_image_refs": missing_summary_refs,
                "summary_checks": summary_checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
