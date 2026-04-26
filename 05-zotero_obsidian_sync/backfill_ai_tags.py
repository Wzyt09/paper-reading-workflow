from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import sync_pipeline as sp


TAG_LINE_RE = re.compile(r"^\s*[-*]\s*关键\s*tags\s*[：:]\s*(.+?)\s*$", re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill the '关键tags' field in existing summary markdown files."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config.json",
        help="Path to config.json",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        nargs="*",
        help="Optional explicit summary markdown paths. Defaults to all root-level summaries.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing 关键tags lines instead of skipping them.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for the number of files to process.",
    )
    return parser.parse_args()


def discover_summary_files(config: sp.Config, explicit: list[Path] | None) -> list[Path]:
    if explicit:
        resolved: list[Path] = []
        for path in explicit:
            candidate = path.expanduser()
            if not candidate.is_absolute():
                candidate = (config.workspace_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            resolved.append(candidate)
        return resolved
    return sorted(
        path.resolve()
        for path in config.summary_root.glob("*.md")
        if path.is_file() and not path.name.startswith("总览")
    )


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def normalize_tags(raw_tags: list[str]) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for raw in raw_tags:
        tag = str(raw).strip().strip("#").strip()
        tag = re.sub(r"\s+", " ", tag)
        tag = tag.strip("，,、；;|/ ")
        if not tag:
            continue
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags[:5]


def parse_model_tags(raw_text: str) -> list[str]:
    text = strip_code_fences(raw_text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return normalize_tags([str(item) for item in parsed])
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return normalize_tags([str(item) for item in parsed])
        except json.JSONDecodeError:
            pass

    candidates: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*[-*\d.]+\s*", "", line).strip()
        if cleaned:
            candidates.extend(part for part in re.split(r"[,，、;；|/]+", cleaned) if part.strip())
    return normalize_tags(candidates)


def build_tag_prompt(summary_path: Path, summary_text: str) -> list[dict[str, object]]:
    truncated = summary_text
    if len(truncated) > 24000:
        truncated = truncated[:24000] + "\n\n[... 已截断，仅保留前 24000 字符用于提炼 tags ...]\n"
    system_prompt = (
        "你是论文标签提炼助手。"
        "请从给定的论文总结 Markdown 中提炼 5 个最关键的中文 tags。"
        "输出必须且只能是 JSON 数组，例如 "
        '["里德堡原子","量子纠错","中性原子","魔态蒸馏","容错计算"]。'
        "不要输出解释，不要输出 Markdown，不要加代码块。"
    )
    user_prompt = (
        f"目标文件：{summary_path.name}\n\n"
        "要求：\n"
        "- 恰好输出 5 个 tag。\n"
        "- 尽量具体，优先反映物理系统、核心机制、方法或应用。\n"
        "- 不要带 #、斜杠、编号。\n"
        "- 避免“论文总结”“实验结果”这类空泛词。\n\n"
        f"以下是总结 Markdown：\n{truncated}"
    )
    return [
        {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
        {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
    ]


def upsert_tag_line(summary_text: str, tags: list[str]) -> str:
    tag_line = f"- 关键tags：{'，'.join(tags)}"
    if TAG_LINE_RE.search(summary_text):
        return TAG_LINE_RE.sub(tag_line, summary_text, count=1)

    lines = summary_text.splitlines()
    info_idx = next((idx for idx, line in enumerate(lines) if line.strip() == "## 论文信息"), -1)
    if info_idx == -1:
        raise sp.SyncError("Could not find '## 论文信息' section.")

    next_heading_idx = len(lines)
    for idx in range(info_idx + 1, len(lines)):
        if lines[idx].startswith("## "):
            next_heading_idx = idx
            break

    insert_idx = next_heading_idx
    while insert_idx > info_idx + 1 and lines[insert_idx - 1].strip() == "":
        insert_idx -= 1
    lines.insert(insert_idx, tag_line)
    if insert_idx + 1 < len(lines) and lines[insert_idx + 1].strip() != "":
        lines.insert(insert_idx + 1, "")
    return "\n".join(lines).rstrip() + "\n"


def process_summary(config: sp.Config, summary_path: Path, *, overwrite: bool) -> tuple[str, list[str] | None]:
    text = sp.read_text(summary_path)
    if not overwrite:
        existing = sp.extract_ai_tags_from_md(summary_path)
        if existing:
            return "skip-existing", existing

    payload = build_tag_prompt(summary_path, text)
    result_text = sp.call_model(config, payload)
    tags = parse_model_tags(result_text)
    if len(tags) != 5:
        raise sp.SyncError(f"Expected 5 tags, got {len(tags)} from model output: {result_text!r}")

    updated = upsert_tag_line(text, tags)
    if updated != text:
        summary_path.write_text(updated, encoding="utf-8")
        return "updated", tags
    return "unchanged", tags


def main() -> int:
    args = parse_args()
    config = sp.load_config(args.config)
    summaries = discover_summary_files(config, args.summary)
    if args.limit and args.limit > 0:
        summaries = summaries[: args.limit]

    updated = 0
    skipped = 0
    errors = 0
    for summary_path in summaries:
        try:
            status, tags = process_summary(config, summary_path, overwrite=args.overwrite)
            if status == "updated":
                updated += 1
                sp.log(f"[tag-backfill] updated {summary_path.name} -> {', '.join(tags or [])}")
            elif status == "skip-existing":
                skipped += 1
                sp.log(f"[tag-backfill] skip {summary_path.name} (already has tags)")
            else:
                sp.log(f"[tag-backfill] unchanged {summary_path.name}")
        except Exception as exc:
            errors += 1
            sp.log(f"[tag-backfill-error] {summary_path.name}: {exc}")

    sp.log(
        f"[tag-backfill] done: total={len(summaries)} updated={updated} skipped={skipped} errors={errors}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
