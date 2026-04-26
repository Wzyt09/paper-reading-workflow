from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import mimetypes
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

import fitz


DEFAULT_DOCLING_URL = os.environ.get("DOCLING_SERVE_URL", "http://192.168.31.211:5001").strip().rstrip("/")
DEFAULT_DOCLING_TIMEOUT = int(os.environ.get("DOCLING_SERVE_TIMEOUT", "240") or "240")
DEFAULT_ENABLE_DOCLING = os.environ.get("PDF_EXTRACT_ENABLE_DOCLING", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEFAULT_DISABLE_DOCLING = not DEFAULT_ENABLE_DOCLING
EXTRACTOR_VERSION = 3
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".svg",
}
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HTML_IMAGE_RE = re.compile(r'(<img\b[^>]*\bsrc=")([^"]+)(")', re.IGNORECASE)
DATA_URI_RE = re.compile(r"^data:(image/[-+\w.]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
IMAGE_LINE_RE = re.compile(r"!\[[^\]]*\]\((assets/[^)]+)\)")
FIGURE_CAPTION_RE = re.compile(
    r"^(?:(?:FIG|Fig)\.?|Figure)\s*((?:S)?\d+)(?:\s*\([^)]+\))?\s*(?:[|.:])?\s*(.*)",
    re.IGNORECASE,
)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w.-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-") or "document"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract text, markdown, page renders, and embedded images from a PDF."
    )
    parser.add_argument("pdf", type=Path, help="Path to the source PDF file.")
    parser.add_argument(
        "--outdir",
        type=Path,
        help="Output directory. Defaults to 03-tools/pdf_tools/output/<pdf-stem>/",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=170,
        help="Resolution for rendered page images. Default: 170.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the output directory before writing new files.",
    )
    parser.add_argument(
        "--docling-url",
        default=DEFAULT_DOCLING_URL,
        help="Preferred Docling Serve base URL. Default: %(default)s",
    )
    parser.add_argument(
        "--docling-timeout",
        type=int,
        default=DEFAULT_DOCLING_TIMEOUT,
        help="Docling Serve timeout in seconds. Default: %(default)s",
    )
    parser.add_argument(
        "--enable-docling",
        action="store_true",
        help="Use Docling Serve before local extraction backends.",
    )
    parser.add_argument(
        "--disable-docling",
        action="store_true",
        help="Skip Docling Serve and use only local extraction backends.",
    )
    args = parser.parse_args()
    if args.enable_docling and args.disable_docling:
        parser.error("Choose at most one of --enable-docling or --disable-docling.")
    if args.enable_docling:
        args.disable_docling = False
    elif args.disable_docling:
        args.disable_docling = True
    else:
        args.disable_docling = DEFAULT_DISABLE_DOCLING
    return args


def default_outdir(pdf_path: Path) -> Path:
    base_dir = Path(__file__).resolve().parent / "output"
    return base_dir / slugify(pdf_path.stem)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def build_text_output(doc: fitz.Document) -> str:
    parts: list[str] = []
    for page_number, page in enumerate(doc, start=1):
        page_text = page.get_text("text", sort=True).strip()
        parts.append(f"===== Page {page_number} =====\n")
        parts.append(page_text if page_text else "[No extractable text found]")
        parts.append("\n\n")
    return "".join(parts)


def write_text_output(text: str, destination: Path) -> None:
    ensure_parent(destination)
    destination.write_text(text, encoding="utf-8")


def build_markdown_fallback(doc: fitz.Document) -> str:
    title = doc.metadata.get("title") or "Extracted PDF"
    parts: list[str] = [f"# {title}\n\n"]

    for page_number, page in enumerate(doc, start=1):
        page_text = page.get_text("text", sort=True).strip()
        parts.append(f"## Page {page_number}\n\n")
        if page_text:
            parts.append(page_text)
            parts.append("\n\n")
        else:
            parts.append("_No extractable text found on this page._\n\n")

    return "".join(parts)


def unique_output_path(directory: Path, suggested_name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    base = Path(suggested_name)
    stem = slugify(base.stem)
    suffix = base.suffix.lower()
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = directory / f"{stem}-{counter:02d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def copy_asset(source: Path, assets_dir: Path) -> str:
    if source.parent.resolve() == assets_dir.resolve():
        return source.name
    target = unique_output_path(assets_dir, source.name)
    shutil.copyfile(source, target)
    return target.name


def decode_data_uri(ref: str) -> tuple[str, bytes] | None:
    match = DATA_URI_RE.match(ref.strip())
    if not match:
        return None
    mime_type = match.group(1)
    payload = match.group(2)
    extension = mimetypes.guess_extension(mime_type) or ".bin"
    return extension, base64.b64decode(payload)


def rewrite_markdown_assets(
    markdown: str,
    *,
    assets_dir: Path,
    source_dir: Path | None,
) -> tuple[str, list[str]]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    rewritten_assets: list[str] = []
    ref_cache: dict[str, str] = {}
    inline_counter = 0

    def rewrite_ref(ref: str) -> str:
        nonlocal inline_counter
        key = ref.strip()
        if key in ref_cache:
            return f"assets/{ref_cache[key]}"
        if key.startswith(("http://", "https://", "file://")):
            return ref

        decoded_data_uri = decode_data_uri(key)
        if decoded_data_uri is not None:
            extension, raw_bytes = decoded_data_uri
            inline_counter += 1
            target = unique_output_path(assets_dir, f"docling-asset-{inline_counter:04d}{extension}")
            target.write_bytes(raw_bytes)
            ref_cache[key] = target.name
            rewritten_assets.append(target.name)
            return f"assets/{target.name}"

        if source_dir is None:
            return ref

        normalized = urllib.parse.unquote(key.split("#", 1)[0].split("?", 1)[0]).strip()
        normalized = normalized.strip("<>").replace("\\", "/")
        candidate = (source_dir / normalized).resolve()
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS:
            copied_name = copy_asset(candidate, assets_dir)
            ref_cache[key] = copied_name
            rewritten_assets.append(copied_name)
            return f"assets/{copied_name}"
        return ref

    def markdown_image_repl(match: re.Match[str]) -> str:
        alt_text, ref = match.groups()
        return f"![{alt_text}]({rewrite_ref(ref)})"

    def html_image_repl(match: re.Match[str]) -> str:
        prefix, ref, suffix = match.groups()
        return f"{prefix}{rewrite_ref(ref)}{suffix}"

    rewritten = MARKDOWN_IMAGE_RE.sub(markdown_image_repl, markdown)
    rewritten = HTML_IMAGE_RE.sub(html_image_repl, rewritten)
    return rewritten, rewritten_assets


def is_picture_text_line(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.startswith("**----- Start of picture text -----**")
        or stripped.startswith("**----- End of picture text -----**")
        or stripped == "<br>"
        or stripped.endswith("<br>")
    )


def normalize_caption_line(line: str) -> str:
    stripped = line.strip()
    stripped = re.sub(r"^[>*_\s`|]+", "", stripped)
    stripped = re.sub(r"[*_`\s]+$", "", stripped)
    return stripped.strip()


def match_figure_caption(line: str) -> re.Match[str] | None:
    return FIGURE_CAPTION_RE.match(normalize_caption_line(line))


def collect_figure_ref_indices_before(lines: list[str], line_index: int) -> list[int]:
    indices: list[int] = []
    cursor = line_index - 1
    while cursor >= 0 and line_index - cursor <= 25:
        candidate = lines[cursor].strip()
        if match_figure_caption(candidate):
            break
        if IMAGE_LINE_RE.fullmatch(candidate):
            indices.append(cursor)
        elif indices and candidate and not is_picture_text_line(candidate):
            break
        cursor -= 1
    indices.reverse()
    return indices


def collect_figure_ref_indices_after(lines: list[str], line_index: int) -> list[int]:
    indices: list[int] = []
    content_lines_before_first_ref = 0
    cursor = line_index + 1
    while cursor < len(lines) and cursor - line_index <= 25:
        candidate = lines[cursor].strip()
        if match_figure_caption(candidate):
            break
        if IMAGE_LINE_RE.fullmatch(candidate):
            indices.append(cursor)
        elif candidate and not is_picture_text_line(candidate):
            if indices:
                break
            content_lines_before_first_ref += 1
            if content_lines_before_first_ref > 2:
                break
        cursor += 1
    return indices


def markdown_image_ref_from_line(line: str) -> str | None:
    match = IMAGE_LINE_RE.fullmatch(line.strip())
    if not match:
        return None
    return match.group(1)


def image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with fitz.open(str(path)) as image_doc:
            if image_doc.page_count < 1:
                return None
            rect = image_doc[0].rect
            return int(round(rect.width)), int(round(rect.height))
    except Exception:
        return None


def representative_page_dimensions(pages_dir: Path) -> tuple[int, int] | None:
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


def search_caption_rect(doc: fitz.Document, figure_label: str) -> tuple[int, fitz.Rect] | None:
    tokens = [
        f"Figure {figure_label}",
        f"FIG. {figure_label}",
        f"Fig. {figure_label}",
        f"Fig {figure_label}",
    ]
    for page_index, page in enumerate(doc):
        for token in tokens:
            hits = page.search_for(token)
            if hits:
                return page_index, hits[0]
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

    include_caption = False
    if candidates:
        x0 = min(rect.x0 for rect in candidates)
        y0 = min(rect.y0 for rect in candidates)
        x1 = max(rect.x1 for rect in candidates)
        y1 = max(rect.y1 for rect in candidates)
        if 0 <= caption_rect.y0 - y1 <= 48:
            include_caption = True
            y1 = max(y1, caption_rect.y1 + 8)
        rect = fitz.Rect(x0 - 12, y0 - 12, x1 + 12, y1 + 12)
        rect = rect & page_rect
        return rect if rect.width > 16 and rect.height > 16 else None

    top = max(0.0, caption_rect.y0 - min(420, page_rect.height * 0.45))
    bottom = caption_rect.y1 + 8 if include_caption else max(top + 40, caption_rect.y0 - 8)
    rect = fitz.Rect(x_limits[0], top, x_limits[1], bottom)
    rect = rect & page_rect
    return rect if rect.width > 16 and rect.height > 16 else None


def crop_figure_from_document(doc: fitz.Document, figure_label: str, assets_dir: Path) -> str | None:
    search_result = search_caption_rect(doc, figure_label)
    if search_result is None:
        return None
    page_index, caption_rect = search_result
    page = doc[page_index]
    clip = figure_crop_rect(page, caption_rect)
    if clip is None:
        return None
    assets_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_output_path(assets_dir, f"fig-{slugify(figure_label)}-crop.png")
    page.get_pixmap(clip=clip, dpi=220, alpha=False).save(output_path)
    return output_path.name


def replace_or_insert_figure_ref(
    lines: list[str],
    line_index: int,
    ref_indices: list[int],
    new_ref: str,
) -> int:
    image_line = f"![]({new_ref})"
    if ref_indices:
        lines[ref_indices[0]] = image_line
        for index in ref_indices[1:]:
            lines[index] = ""
        return 0
    lines.insert(line_index, image_line)
    lines.insert(line_index + 1, "")
    return 2


def postprocess_markdown_figure_assets(
    doc: fitz.Document,
    markdown_path: Path,
    assets_dir: Path,
    pages_dir: Path,
) -> dict[str, Any]:
    if not markdown_path.is_file() or not assets_dir.is_dir():
        return {"status": "skipped"}

    lines = markdown_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return {"status": "empty"}

    page_dimensions_hint = representative_page_dimensions(pages_dir)
    crops_created = 0
    refs_replaced = 0
    refs_inserted = 0
    changed = False
    line_index = 0

    while line_index < len(lines):
        match = match_figure_caption(lines[line_index])
        if not match:
            line_index += 1
            continue

        figure_label = match.group(1).upper()
        before_indices = collect_figure_ref_indices_before(lines, line_index)
        after_indices = collect_figure_ref_indices_after(lines, line_index)
        ref_indices = before_indices or after_indices
        refs = [markdown_image_ref_from_line(lines[index]) for index in ref_indices]
        refs = [ref for ref in refs if ref]

        usable_refs = []
        for ref in refs:
            asset_path = markdown_path.parent / ref
            if asset_path.is_file() and is_probable_figure_asset(asset_path, page_dimensions_hint):
                usable_refs.append(ref)

        if usable_refs:
            line_index += 1
            continue

        cropped_name = crop_figure_from_document(doc, figure_label, assets_dir)
        if not cropped_name:
            line_index += 1
            continue

        changed = True
        crops_created += 1
        if ref_indices:
            refs_replaced += 1
        else:
            refs_inserted += 1
        line_index += replace_or_insert_figure_ref(lines, line_index, ref_indices, f"assets/{cropped_name}")
        line_index += 1

    if changed:
        rewritten = "\n".join(line for line in lines if line is not None).rstrip() + "\n"
        markdown_path.write_text(rewritten, encoding="utf-8")

    return {
        "status": "changed" if changed else "unchanged",
        "crops_created": crops_created,
        "refs_replaced": refs_replaced,
        "refs_inserted": refs_inserted,
    }


def select_primary_file(paths: list[Path], preferred_names: Iterable[str]) -> Path | None:
    preferred_order = {name.lower(): index for index, name in enumerate(preferred_names)}
    if not paths:
        return None

    def sort_key(path: Path) -> tuple[int, int, int]:
        return (
            preferred_order.get(path.name.lower(), len(preferred_order)),
            len(path.parts),
            len(path.as_posix()),
        )

    return sorted(paths, key=sort_key)[0]


def parse_docling_zip_payload(outdir: Path, raw_zip: bytes) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
            archive.extractall(temp_root)

        markdown_candidates = list(temp_root.rglob("*.md"))
        markdown_path = select_primary_file(markdown_candidates, ("document.md", "output.md", "content.md"))
        if markdown_path is None:
            raise RuntimeError("Docling zip response did not contain a markdown file.")

        text_candidates = list(temp_root.rglob("*.txt"))
        text_path = select_primary_file(text_candidates, ("document.txt", "output.txt", "content.txt"))

        raw_markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
        assets_dir = outdir / "assets"
        normalized_markdown, asset_names = rewrite_markdown_assets(
            raw_markdown,
            assets_dir=assets_dir,
            source_dir=markdown_path.parent,
        )
        return {
            "markdown": normalized_markdown.rstrip() + "\n",
            "text_content": text_path.read_text(encoding="utf-8", errors="replace") if text_path else "",
            "asset_names": asset_names,
        }


def parse_docling_inbody_payload(outdir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    document = payload.get("document")
    if not isinstance(document, dict):
        raise RuntimeError(f"Unexpected Docling JSON response: {payload!r}")

    raw_markdown = str(document.get("md_content") or "")
    if not raw_markdown.strip():
        raise RuntimeError("Docling JSON response did not include md_content.")

    assets_dir = outdir / "assets"
    normalized_markdown, asset_names = rewrite_markdown_assets(
        raw_markdown,
        assets_dir=assets_dir,
        source_dir=None,
    )

    (outdir / "docling.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "markdown": normalized_markdown.rstrip() + "\n",
        "text_content": str(document.get("text_content") or ""),
        "asset_names": asset_names,
    }


def http_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes,
    timeout: int,
) -> tuple[dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {k: v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body_text[:1200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def multipart_form_data(
    fields: Iterable[tuple[str, str]],
    file_field: str,
    filename: str,
    data: bytes,
) -> tuple[str, bytes]:
    boundary = f"----CodexBoundary{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields:
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(data)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


def parse_docling_response(outdir: Path, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    content_type = headers.get("Content-Type", "").lower()
    if "application/zip" in content_type or body.startswith(b"PK\x03\x04"):
        return parse_docling_zip_payload(outdir, body)

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unexpected Docling response type: {content_type or 'unknown'}") from exc

    if isinstance(payload, dict) and "document" in payload:
        return parse_docling_inbody_payload(outdir, payload)

    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("detail") or payload.get("error_message") or payload)
    raise RuntimeError(detail or f"Unexpected Docling JSON response: {payload!r}")


def build_docling_fields(
    *,
    target_type: str,
    image_export_mode: str,
    timeout: int,
) -> list[tuple[str, str]]:
    return [
        ("target_type", target_type),
        ("to_formats", "md"),
        ("to_formats", "text"),
        ("image_export_mode", image_export_mode),
        ("include_images", "true"),
        ("do_formula_enrichment", "true"),
        ("do_ocr", "true"),
        ("pdf_backend", "docling_parse"),
        ("document_timeout", str(max(timeout, 60))),
        ("md_page_break_placeholder", ""),
    ]


def clear_docling_artifacts(outdir: Path) -> None:
    shutil.rmtree(outdir / "assets", ignore_errors=True)
    docling_json = outdir / "docling.json"
    if docling_json.exists():
        docling_json.unlink()


def try_docling_sync(
    pdf_path: Path,
    outdir: Path,
    *,
    base_url: str,
    timeout: int,
    target_type: str,
    image_export_mode: str,
) -> dict[str, Any]:
    fields = build_docling_fields(
        target_type=target_type,
        image_export_mode=image_export_mode,
        timeout=timeout,
    )
    content_type, body = multipart_form_data(
        fields,
        "files",
        pdf_path.name,
        pdf_path.read_bytes(),
    )
    headers, raw_response = http_request(
        f"{base_url}/v1/convert/file",
        method="POST",
        headers={"Content-Type": content_type},
        body=body,
        timeout=timeout,
    )
    return parse_docling_response(outdir, headers, raw_response)


def try_docling_async(
    pdf_path: Path,
    outdir: Path,
    *,
    base_url: str,
    timeout: int,
    target_type: str,
    image_export_mode: str,
) -> dict[str, Any]:
    fields = build_docling_fields(
        target_type=target_type,
        image_export_mode=image_export_mode,
        timeout=timeout,
    )
    content_type, body = multipart_form_data(
        fields,
        "files",
        pdf_path.name,
        pdf_path.read_bytes(),
    )
    _, raw_response = http_request(
        f"{base_url}/v1/convert/file/async",
        method="POST",
        headers={"Content-Type": content_type},
        body=body,
        timeout=max(30, min(timeout, 120)),
    )
    payload = json.loads(raw_response.decode("utf-8"))
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"Docling async submission returned no task_id: {payload!r}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(1, int(deadline - time.monotonic()))
        wait_seconds = min(10, remaining)
        _, poll_response = http_request(
            f"{base_url}/v1/status/poll/{task_id}?wait={wait_seconds}",
            method="GET",
            headers={},
            body=b"",
            timeout=wait_seconds + 20,
        )
        poll_payload = json.loads(poll_response.decode("utf-8"))
        status = str(poll_payload.get("task_status") or "").strip().lower()
        if status == "completed":
            result_headers, result_response = http_request(
                f"{base_url}/v1/result/{task_id}",
                method="GET",
                headers={},
                body=b"",
                timeout=max(60, timeout),
            )
            return parse_docling_response(outdir, result_headers, result_response)
        if status == "failed":
            raise RuntimeError(
                str(poll_payload.get("error_message") or "Docling async conversion failed.")
            )
        if "detail" in poll_payload:
            raise RuntimeError(str(poll_payload["detail"]))

    raise TimeoutError(f"Docling async conversion timed out after {timeout} seconds.")


def extract_with_docling(
    pdf_path: Path,
    outdir: Path,
    *,
    base_url: str,
    timeout: int,
) -> dict[str, Any]:
    attempts: list[dict[str, str]] = []
    strategies = [
        ("docling-serve-zip", try_docling_sync, "zip", "referenced"),
        ("docling-serve-inbody", try_docling_sync, "inbody", "embedded"),
        ("docling-serve-async-zip", try_docling_async, "zip", "referenced"),
        ("docling-serve-async-inbody", try_docling_async, "inbody", "embedded"),
    ]

    for method_name, runner, target_type, image_export_mode in strategies:
        clear_docling_artifacts(outdir)
        try:
            result = runner(
                pdf_path,
                outdir,
                base_url=base_url,
                timeout=timeout,
                target_type=target_type,
                image_export_mode=image_export_mode,
            )
            attempts.append({"method": method_name, "status": "success"})
            result["method"] = method_name
            result["details"] = {
                "docling_url": base_url,
                "attempts": attempts,
                "target_type": target_type,
                "image_export_mode": image_export_mode,
            }
            return result
        except Exception as exc:
            attempts.append({"method": method_name, "status": "failed", "error": str(exc)})

    clear_docling_artifacts(outdir)
    failure_summary = "; ".join(
        f"{item['method']}: {item.get('error', item['status'])}" for item in attempts
    )
    raise RuntimeError(f"All Docling Serve attempts failed: {failure_summary}")


def write_markdown_output(
    pdf_path: Path,
    doc: fitz.Document,
    destination: Path,
    dpi: int,
    *,
    docling_url: str,
    docling_timeout: int,
    disable_docling: bool,
) -> tuple[str, str, dict[str, Any]]:
    ensure_parent(destination)
    extraction_details: dict[str, Any] = {
        "docling_url": docling_url,
        "docling_enabled": not disable_docling and bool(docling_url),
    }

    if not disable_docling and docling_url:
        try:
            result = extract_with_docling(
                pdf_path,
                destination.parent,
                base_url=docling_url,
                timeout=docling_timeout,
            )
            destination.write_text(result["markdown"], encoding="utf-8")
            extraction_details.update(result.get("details", {}))
            return result["method"], str(result.get("text_content") or ""), extraction_details
        except Exception as exc:
            clear_docling_artifacts(destination.parent)
            extraction_details["docling_error"] = str(exc)

    method = "fallback"
    markdown = ""
    try:
        import pymupdf4llm

        assets_dir = destination.parent / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        with pushd(destination.parent):
            markdown = pymupdf4llm.to_markdown(
                str(pdf_path),
                write_images=True,
                image_path="assets",
                dpi=dpi,
                force_text=True,
            )
        if markdown.strip():
            method = "pymupdf4llm"
    except Exception as exc:
        extraction_details["pymupdf4llm_error"] = str(exc)
        markdown = ""

    if not markdown.strip():
        markdown = build_markdown_fallback(doc)
        method = "fitz-text"

    destination.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    extraction_details["figure_asset_postprocess"] = postprocess_markdown_figure_assets(
        doc,
        destination,
        destination.parent / "assets",
        destination.parent / "pages",
    )
    return method, "", extraction_details


@contextlib.contextmanager
def pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def render_page_images(doc: fitz.Document, pages_dir: Path, dpi: int) -> list[Path]:
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_paths: list[Path] = []
    for page_number, page in enumerate(doc, start=1):
        output_path = pages_dir / f"page-{page_number:03d}.png"
        page.get_pixmap(dpi=dpi, alpha=False).save(output_path)
        page_paths.append(output_path)
    return page_paths


def extract_embedded_images(doc: fitz.Document, embedded_dir: Path) -> list[dict[str, Any]]:
    embedded_dir.mkdir(parents=True, exist_ok=True)
    seen_xrefs: set[int] = set()
    extracted: list[dict[str, Any]] = []

    for page_number, page in enumerate(doc, start=1):
        for image_index, image_info in enumerate(page.get_images(full=True), start=1):
            xref = image_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            image = doc.extract_image(xref)
            extension = image.get("ext", "bin")
            output_path = embedded_dir / f"page-{page_number:03d}-img-{image_index:02d}.{extension}"
            output_path.write_bytes(image["image"])
            extracted.append(
                {
                    "page": page_number,
                    "xref": xref,
                    "path": output_path.name,
                    "width": image.get("width"),
                    "height": image.get("height"),
                    "colorspace": image.get("colorspace"),
                }
            )

    return extracted


def write_metadata(
    doc: fitz.Document,
    pdf_path: Path,
    destination: Path,
    markdown_method: str,
    page_image_paths: list[Path],
    embedded_images: list[dict[str, Any]],
    asset_paths: list[Path],
    extraction_details: dict[str, Any],
) -> None:
    ensure_parent(destination)
    payload = {
        "extractor_version": EXTRACTOR_VERSION,
        "source_pdf": str(pdf_path.resolve()),
        "page_count": doc.page_count,
        "metadata": doc.metadata,
        "markdown_method": markdown_method,
        "page_images": [path.name for path in page_image_paths],
        "markdown_assets": [path.name for path in asset_paths],
        "embedded_images": embedded_images,
        "extraction_details": extraction_details,
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf.expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    outdir = args.outdir.expanduser().resolve() if args.outdir else default_outdir(pdf_path)
    if args.clean and outdir.exists():
        shutil.rmtree(outdir)

    outdir.mkdir(parents=True, exist_ok=True)
    pages_dir = outdir / "pages"
    embedded_dir = outdir / "embedded"

    with fitz.open(pdf_path) as doc:
        page_image_paths = render_page_images(doc, pages_dir, dpi=args.dpi)
        embedded_images = extract_embedded_images(doc, embedded_dir)
        markdown_path = outdir / "document.md"
        markdown_method, docling_text, extraction_details = write_markdown_output(
            pdf_path,
            doc,
            markdown_path,
            args.dpi,
            docling_url=args.docling_url,
            docling_timeout=args.docling_timeout,
            disable_docling=args.disable_docling,
        )
        text_path = outdir / "document.txt"
        extracted_text = docling_text.strip() or build_text_output(doc)
        write_text_output(extracted_text, text_path)
        asset_paths = sorted((outdir / "assets").glob("*")) if (outdir / "assets").exists() else []
        metadata_path = outdir / "metadata.json"
        write_metadata(
            doc,
            pdf_path,
            metadata_path,
            markdown_method,
            page_image_paths,
            embedded_images,
            asset_paths,
            extraction_details,
        )

    summary = {
        "source_pdf": str(pdf_path),
        "outdir": str(outdir),
        "markdown_method": markdown_method,
        "files": [
            "document.txt",
            "document.md",
            "metadata.json",
            f"assets/{len(asset_paths)} markdown image asset(s)",
            f"pages/{len(page_image_paths)} rendered page image(s)",
            f"embedded/{len(embedded_images)} extracted embedded image(s)",
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
