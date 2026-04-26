from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import sync_pipeline as sp


CONNECTOR_BASE = "http://127.0.0.1:23119"
REFRESH_INTERVAL_MS = 5000

SUMMARY_STAGE_LABELS = {
    "summary-task": "准备处理选中文献",
    "run": "启动总结流程",
    "match": "匹配 Zotero 元数据",
    "scan": "扫描 PDF",
    "extract": "提取 PDF 内容",
    "summarize": "调用 Codex 生成总结",
    "package": "打包总结结果",
    "obsidian": "同步到 Obsidian",
    "zotero": "回写 Zotero 附件",
    "done": "处理完成",
}

SUMMARY_STAGE_DETAILS = {
    "准备处理选中文献": "已接收到你选中的文献，正在准备逐篇调用默认总结流程。",
    "准备处理当前文献": "已经切换到当前这篇文献，马上开始执行提取和总结。",
    "启动总结流程": "正在启动 sync_pipeline.py，并加载当前最新的 default.md 规范。",
    "匹配 Zotero 元数据": "正在把这篇 PDF 和 Zotero 条目、已有状态记录进行匹配。",
    "扫描 PDF": "正在检查本次要处理的 PDF 输入，并确认实际文件路径。",
    "提取 PDF 内容": "正在抽取 PDF 的文本、结构和后续总结所需的中间材料。",
    "调用 Codex 生成总结": "正在根据默认规范生成总结正文。这一步通常最耗时。",
    "打包总结结果": "正在整理 markdown、材料文件和输出目录。",
    "同步到 Obsidian": "正在把结果写入统一的 Obsidian 导出目录。",
    "回写 Zotero 附件": "正在把总结文档作为链接附件挂回 Zotero 条目。",
    "处理完成": "当前文献的总结、打包和同步已经完成。",
    "缺少可用的总结后端": "当前没有可用的 OpenAI API 或 Codex CLI，因此无法自动生成新总结。",
    "处理失败": "流程中有步骤报错。请查看下方最近输出定位具体失败点。",
    "已停止": "你已经手动停止当前任务。",
    "已完成": "本次选中的全部文献都已经处理完成。",
}

GUI_ERROR_LOG_PATH = SCRIPT_DIR / ".state" / "paper_sync_gui_error.log"
GUI_STARTUP_LOG_PATH = SCRIPT_DIR / ".state" / "paper_sync_gui_startup.log"
SUMMARY_RUNTIME_PREFS_PATH = SCRIPT_DIR / ".state" / "gui_summary_runtime.json"
SUMMARY_BACKEND_LABELS = {
    "openai": "OpenAI API",
    "openai_compatible": "OpenAI-compatible API",
    "codex": "Codex CLI",
}
SUMMARY_REASONING_OPTIONS = ("low", "medium", "high", "xhigh")
SUMMARY_MODEL_OPTIONS = {
    "openai": (
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
    ),
    "openai_compatible": (
        "deepseek-chat",
        "deepseek-reasoner",
        "qwen-plus",
        "qwen-max",
        "moonshot-v1-32k",
        "glm-4-plus",
    ),
    "codex": (
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
        "gpt-5.3-codex",
    ),
}
SUMMARY_BACKEND_HINTS = {
    "openai": "OpenAI API: 使用 Responses API，适合稳定批量生成。GPT-5.5 暂不作为 API 默认选项。",
    "openai_compatible": "OpenAI-compatible: 使用 /v1/chat/completions，可接 DeepSeek、通义千问、Kimi、智谱等兼容接口。",
    "codex": "Codex CLI: 使用本机 Codex，可选 GPT-5.5，适合最高质量和长上下文任务。",
}
SUMMARY_MODEL_DETAILS = {
    "gpt-5.5": {
        "profile": "最高质量",
        "use": "Codex 可用；适合最难论文、跨章节综合和高要求推理。",
    },
    "gpt-5.4": {
        "profile": "高质量",
        "use": "适合复杂论文、公式较多或需要结构化判断的总结任务。",
    },
    "gpt-5.4-mini": {
        "profile": "均衡",
        "use": "适合大多数日常论文总结，速度和质量比较平衡。",
    },
    "gpt-5.4-nano": {
        "profile": "最快/低成本",
        "use": "适合快速初筛、短论文或只需要粗略摘要的任务。",
    },
    "gpt-5.2": {
        "profile": "稳定旧档",
        "use": "保留给已有流程和账户配置；质量通常低于 GPT-5.4 系列。",
    },
    "gpt-5.3-codex": {
        "profile": "Codex 旧档",
        "use": "偏结构化技术任务；保留用于兼容既有 Codex 配置。",
    },
    "deepseek-chat": {
        "profile": "低成本",
        "use": "适合常规论文总结和批量初筛，价格通常较低。",
    },
    "deepseek-reasoner": {
        "profile": "推理",
        "use": "适合需要推导、机制比较和长链路判断的总结任务。",
    },
    "qwen-plus": {
        "profile": "国内均衡",
        "use": "适合中文输出、常规总结和较低成本批处理。",
    },
    "qwen-max": {
        "profile": "国内高质量",
        "use": "适合复杂论文、跨文献对比和更高质量中文表达。",
    },
    "moonshot-v1-32k": {
        "profile": "长上下文",
        "use": "适合较长输入和中文阅读工作流。",
    },
    "glm-4-plus": {
        "profile": "国内通用",
        "use": "适合中文总结和结构化问答。",
    },
}
SUMMARY_MODEL_COMMENTS = {
    "gpt-5.5": "速度: 最慢 | 推理: 最强 | Codex 可用，适合最高要求的论文总结。",
    "gpt-5.4": "速度: 慢 | 推理: 很强 | 适合复杂论文、长文和需要结构化判断的总结任务。",
    "gpt-5.4-mini": "速度: 快 | 推理: 强 | 适合大多数日常论文总结，通常是速度和质量最均衡的选择。",
    "gpt-5.4-nano": "速度: 最快 | 推理: 较弱 | 适合快速初筛、短摘要和低成本批处理。",
    "gpt-5.2": "速度: 中 | 推理: 较强 | 适合长流程、稳定性优先的任务，质量通常低于 gpt-5.4 但高于轻量模型。",
    "gpt-5.3-codex": "速度: 中快 | 推理: 强 | 更偏结构化技术任务和工程文本，做论文总结可用，但通用表述通常不如 gpt-5.4 自然。",
    "deepseek-chat": "速度: 快 | 成本: 低 | 适合常规批量论文总结。",
    "deepseek-reasoner": "速度: 中 | 推理: 强 | 适合公式、机制和跨文献比较。",
    "qwen-plus": "速度: 快 | 成本: 较低 | 适合中文总结和批处理。",
    "qwen-max": "速度: 中 | 质量: 高 | 适合更复杂的中文科技文本。",
    "moonshot-v1-32k": "速度: 中 | 上下文: 较长 | 适合长文输入。",
    "glm-4-plus": "速度: 中 | 质量: 通用 | 适合结构化中文总结。",
}
SUMMARY_REASONING_COMMENTS = {
    "low": "速度: 最快 | 推理: 最弱 | 适合粗略初筛、快速看点，不适合复杂推导或精细问答。",
    "medium": "速度: 快 | 推理: 中等 | 适合常规总结，是默认的稳妥档位。",
    "high": "速度: 较慢 | 推理: 强 | 适合涉及公式、机制分析、误差来源梳理的论文。",
    "xhigh": "速度: 最慢 | 推理: 最强 | 适合最复杂的推导、跨章节综合和高要求问答，耗时明显更长。",
}


def open_path(path: Path) -> None:
    os.startfile(str(path))


def load_codex_cli_defaults() -> dict[str, str]:
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    model = str(payload.get("model") or "").strip()
    reasoning = str(payload.get("model_reasoning_effort") or "").strip().lower()
    result: dict[str, str] = {}
    if model:
        result["model"] = model
    if reasoning in SUMMARY_REASONING_OPTIONS:
        result["reasoning"] = reasoning
    return result


def write_startup_log(message: str) -> None:
    try:
        GUI_STARTUP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with GUI_STARTUP_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def load_summary_runtime_prefs() -> dict[str, Any]:
    try:
        if not SUMMARY_RUNTIME_PREFS_PATH.exists():
            return {}
        text = SUMMARY_RUNTIME_PREFS_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_summary_runtime_prefs(payload: dict[str, Any]) -> None:
    SUMMARY_RUNTIME_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    temp_path = SUMMARY_RUNTIME_PREFS_PATH.with_name(
        f"{SUMMARY_RUNTIME_PREFS_PATH.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temp_path.write_text(text, encoding="utf-8")
        try:
            temp_path.replace(SUMMARY_RUNTIME_PREFS_PATH)
        except OSError:
            try:
                SUMMARY_RUNTIME_PREFS_PATH.write_text(text, encoding="utf-8")
            except OSError:
                return
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def infer_summary_stage(message: str) -> str:
    text = message.strip()
    if not text:
        return ""
    if text.startswith("[summary-task]"):
        return SUMMARY_STAGE_LABELS["summary-task"]
    if text.startswith("[run]"):
        return SUMMARY_STAGE_LABELS["run"]
    if text.startswith("[match]"):
        return SUMMARY_STAGE_LABELS["match"]
    if text.startswith("[scan]"):
        return SUMMARY_STAGE_LABELS["scan"]
    if text.startswith("[extract]"):
        return SUMMARY_STAGE_LABELS["extract"]
    if text.startswith("[summarize]"):
        return SUMMARY_STAGE_LABELS["summarize"]
    if text.startswith("[package]"):
        return SUMMARY_STAGE_LABELS["package"]
    if text.startswith("[obsidian]"):
        return SUMMARY_STAGE_LABELS["obsidian"]
    if text.startswith("[zotero]"):
        return SUMMARY_STAGE_LABELS["zotero"]
    if text.startswith("[done]"):
        return SUMMARY_STAGE_LABELS["done"]
    if "No OPENAI_API_KEY found" in text or "codex CLI is unavailable" in text:
        return "缺少可用的总结后端"
    if "failed with exit code" in text.lower():
        return "处理失败"
    return ""


def describe_summary_stage(stage: str) -> str:
    return SUMMARY_STAGE_DETAILS.get(stage, "")


def show_fatal_error(exc: BaseException) -> None:
    try:
        GUI_ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        details = "".join(traceback.format_exception(exc))
        GUI_ERROR_LOG_PATH.write_text(details, encoding="utf-8")
    except Exception:
        details = f"{type(exc).__name__}: {exc}"

    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Paper Sync Manager",
            f"GUI 启动失败，请查看日志：\n{GUI_ERROR_LOG_PATH}\n\n{type(exc).__name__}: {exc}",
        )
    except Exception:
        pass
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def connector_request(
    endpoint: str,
    *,
    method: str = "POST",
    json_body: Any | None = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
) -> tuple[int, bytes]:
    request_headers = dict(headers or {})
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif raw_body is not None:
        data = raw_body

    request = urllib.request.Request(
        f"{CONNECTOR_BASE}{endpoint}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise RuntimeError(
            f"HTTP {exc.code} for {endpoint}: {body.decode('utf-8', errors='replace')[:800]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {endpoint}: {exc}") from exc


def connector_request_json(
    endpoint: str,
    *,
    method: str = "POST",
    json_body: Any | None = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
) -> tuple[int, Any]:
    status, body = connector_request(
        endpoint,
        method=method,
        json_body=json_body,
        raw_body=raw_body,
        headers=headers,
        timeout=timeout,
    )
    if not body:
        return status, None
    return status, json.loads(body.decode("utf-8", errors="replace"))


def connector_ping() -> bool:
    try:
        selected = connector_get_selected_collection()
    except Exception:
        return False
    return isinstance(selected, dict) and bool(selected.get("libraryID"))


def connector_get_selected_collection() -> dict[str, Any]:
    _, payload = connector_request_json("/connector/getSelectedCollection", json_body={})
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected response from /connector/getSelectedCollection")
    return payload


def import_pdf_via_connector(pdf_path: Path) -> dict[str, Any]:
    session_id = uuid.uuid4().hex
    metadata = {
        "sessionID": session_id,
        "title": pdf_path.name,
        "url": pdf_path.resolve().as_uri(),
    }
    status, payload = connector_request_json(
        f"/connector/saveStandaloneAttachment?sessionID={urllib.parse.quote(session_id)}",
        raw_body=pdf_path.read_bytes(),
        headers={
            "Content-Type": "application/pdf",
            # HTTP headers must stay ASCII-safe; escaped JSON still preserves Unicode content.
            "X-Metadata": json.dumps(metadata, ensure_ascii=True),
        },
        timeout=600,
    )
    if status != 201 or not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected saveStandaloneAttachment response: {payload!r}")

    recognized_status, recognized_payload = connector_request_json(
        "/connector/getRecognizedItem",
        json_body={"sessionID": session_id},
        timeout=600,
    )
    recognized = recognized_payload if recognized_status == 200 and isinstance(recognized_payload, dict) else None
    return {
        "session_id": session_id,
        "can_recognize": bool(payload.get("canRecognize")),
        "recognized": recognized,
    }


def state_items_map(state_path: Path) -> dict[str, Any]:
    payload = sp.load_json(state_path, {"items": {}})
    items = payload.get("items", {})
    return items if isinstance(items, dict) else {}


def snapshot_db_and_connect(config: sp.Config) -> tuple[Path, sqlite3.Connection]:
    db_path = sp.discover_zotero_local_db(config)
    if db_path is None:
        raise RuntimeError("Local Zotero DB not found.")
    snapshot_db = sp.snapshot_zotero_local_db(config, db_path)
    return snapshot_db, sqlite3.connect(snapshot_db)


def get_collection_key_by_name(config: sp.Config, collection_name: str) -> str:
    snapshot_db, conn = snapshot_db_and_connect(config)
    try:
        row = conn.execute(
            "select key from collections where collectionName = ? order by collectionID desc limit 1",
            (collection_name,),
        ).fetchone()
        return str(row[0]) if row and row[0] else ""
    finally:
        conn.close()
        shutil.rmtree(snapshot_db.parent, ignore_errors=True)


def ensure_selected_collection(config: sp.Config, collection_name: str, *, timeout_seconds: float = 8.0) -> None:
    current = connector_get_selected_collection()
    if str(current.get("name") or "") == collection_name:
        return
    collection_key = get_collection_key_by_name(config, collection_name)
    if not collection_key:
        raise RuntimeError(f"Collection '{collection_name}' not found in local Zotero DB.")
    os.startfile(f"zotero://select/library/collections/{collection_key}")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            current = connector_get_selected_collection()
            if str(current.get("name") or "") == collection_name:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Failed to switch Zotero to collection '{collection_name}'.")


def resolve_attachment_abs_path(config: sp.Config, attachment_key: str, path_value: str) -> str:
    if not path_value:
        return ""
    if path_value.startswith("storage:"):
        db_path = sp.discover_zotero_local_db(config)
        if db_path is None:
            return ""
        basename = sp.attachment_basename(path_value)
        return str((db_path.parent / "storage" / attachment_key / basename).resolve())
    return str(Path(path_value).resolve())


def load_collection_items(config: sp.Config, paper_paths: list[Path]) -> list[dict[str, Any]]:
    export_records = {record["item_key"]: record for record in sp.load_export_records_from_local_zotero(config)}
    if not export_records:
        return []

    snapshot_db, conn = snapshot_db_and_connect(config)
    try:
        scope_item_ids, _ = sp.fetch_local_zotero_scope_item_ids(
            conn,
            collection_name=config.zotero_local_collection_name,
            fallback_to_all_pdf_items=False,
        )
        if not scope_item_ids:
            return []

        top_rows: list[dict[str, Any]] = []
        attachments_by_parent: dict[int, list[dict[str, Any]]] = {}

        for item_chunk in sp.chunked(scope_item_ids):
            placeholders = sp.sqlite_placeholder_list(item_chunk)
            for row in conn.execute(
                f"""
                select i.itemID, i.key, i.dateAdded, i.dateModified
                from items i
                where i.itemID in ({placeholders})
                order by i.dateAdded desc, i.itemID desc
                """,
                item_chunk,
            ).fetchall():
                top_rows.append(
                    {
                        "item_id": int(row[0]),
                        "item_key": str(row[1]),
                        "date_added": str(row[2] or ""),
                        "date_modified": str(row[3] or ""),
                    }
                )

            for row in conn.execute(
                f"""
                select
                    ia.parentItemID,
                    c.key,
                    ia.linkMode,
                    ia.path,
                    ia.contentType,
                    coalesce((
                        select itemDataValues.value
                        from itemData
                        join fields on fields.fieldID = itemData.fieldID
                        join itemDataValues on itemDataValues.valueID = itemData.valueID
                        where itemData.itemID = c.itemID
                          and fields.fieldName = 'title'
                        limit 1
                    ), '')
                from itemAttachments ia
                join items c on c.itemID = ia.itemID
                where ia.parentItemID in ({placeholders})
                order by ia.parentItemID, c.itemID
                """,
                item_chunk,
            ).fetchall():
                parent_item_id = int(row[0])
                child_key = str(row[1])
                path_value = str(row[3] or "")
                attachments_by_parent.setdefault(parent_item_id, []).append(
                    {
                        "key": child_key,
                        "link_mode": int(row[2] or 0),
                        "path": path_value,
                        "content_type": str(row[4] or ""),
                        "title": str(row[5] or ""),
                        "basename": sp.attachment_basename(path_value) if path_value else "",
                        "abs_path": resolve_attachment_abs_path(config, child_key, path_value),
                    }
                )

        paper_map = {path.name.lower(): path for path in paper_paths}
        state_items = state_items_map(config.state_path)
        state_by_item_key = {
            str(record.get("zotero_item_key") or ""): record
            for record in state_items.values()
            if isinstance(record, dict) and record.get("zotero_item_key")
        }
        rows: list[dict[str, Any]] = []
        for top in top_rows:
            item_key = top["item_key"]
            record = export_records.get(item_key, {})
            title = record.get("title") or item_key
            tags = record.get("tags", [])
            creators = record.get("creators", [])
            authors = sp.display_authors_from_creators(creators)
            year = record.get("year", "")
            attachments = attachments_by_parent.get(top["item_id"], [])
            pdf_attachments = [
                item
                for item in attachments
                if item["content_type"].lower() == "application/pdf" or item["basename"].lower().endswith(".pdf")
            ]
            summary_children = [
                item for item in attachments if item["title"].startswith(sp.SUMMARY_ATTACHMENT_TITLE_PREFIX)
            ]

            preferred_pdf = None
            preferred_label = ""
            for attachment in pdf_attachments:
                basename = attachment["basename"].lower()
                if basename in paper_map:
                    preferred_pdf = paper_map[basename]
                    preferred_label = sp.first_nonempty(
                        str(paper_map[basename].relative_to(config.workspace_root)),
                        attachment["basename"],
                    )
                    break
            if preferred_pdf is None:
                for attachment in pdf_attachments:
                    candidate = Path(attachment["abs_path"]) if attachment["abs_path"] else None
                    if candidate and candidate.exists():
                        preferred_pdf = candidate
                        preferred_label = attachment["basename"] or candidate.name
                        break

            state_record = None
            if preferred_pdf is not None:
                state_record = state_items.get(str(preferred_pdf.resolve()))
            if state_record is None:
                for attachment in pdf_attachments:
                    abs_path = attachment["abs_path"]
                    if abs_path:
                        state_record = state_items.get(str(Path(abs_path).resolve()))
                        if state_record:
                            break
            if state_record is None:
                state_record = state_by_item_key.get(item_key)
            if isinstance(state_record, dict):
                state_record = sp.repair_record_paths(config, state_record)

            rows.append(
                {
                    **top,
                    "title": title,
                    "year": year,
                    "source": record.get("source", ""),
                    "doi": record.get("doi", ""),
                    "arxiv": record.get("arxiv", ""),
                    "citation_key": record.get("citation_key", ""),
                    "tags": tags,
                    "creators": creators,
                    "authors": authors,
                    "pdf_attachments": pdf_attachments,
                    "summary_children": summary_children,
                    "preferred_pdf": str(preferred_pdf.resolve()) if preferred_pdf else "",
                    "preferred_pdf_label": preferred_label,
                    "summary_attached": bool(summary_children),
                    "state_record": state_record if isinstance(state_record, dict) else None,
                }
            )

        rows.sort(key=lambda value: (value.get("date_added", ""), value.get("title", "")), reverse=True)
        return rows
    finally:
        conn.close()
        shutil.rmtree(snapshot_db.parent, ignore_errors=True)


class PaperSyncGUI(tk.Tk):
    def __init__(self) -> None:
        write_startup_log("PaperSyncGUI init begin")
        super().__init__()
        self.title("Paper Sync Manager")
        self.geometry("1400x900")
        self.minsize(1180, 760)
        write_startup_log("Tk root created")

        self.config_data = sp.load_config(SCRIPT_DIR / "config.json")
        write_startup_log("Config loaded")
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.refresh_thread: threading.Thread | None = None
        self.active_process: subprocess.Popen[str] | None = None
        self.stop_requested = threading.Event()
        self.current_task = "Idle"
        self.initial_pdf_set: set[str] | None = None
        self.current_pdfs: list[Path] = []
        self.current_collection_items: list[dict[str, Any]] = []
        self.current_collection_status: str = ""
        self.tree_new_rows: dict[str, Path] = {}
        self.tree_unsynced_rows: dict[str, Path] = {}
        self.tree_collection_rows: dict[str, dict[str, Any]] = {}
        self.file_sha256_cache: dict[tuple[str, int, int], str] = {}
        self.background_task_failed = False
        self.close_after_task = False
        self.summary_progress_window: tk.Toplevel | None = None
        self.summary_progress_label_docs: ttk.Label | None = None
        self.summary_progress_label_title: ttk.Label | None = None
        self.summary_progress_label_pdf: ttk.Label | None = None
        self.summary_progress_label_stage: ttk.Label | None = None
        self.summary_progress_label_detail: ttk.Label | None = None
        self.summary_progress_label_last: ttk.Label | None = None
        self.summary_progress_text: tk.Text | None = None
        self.summary_progress_bar: ttk.Progressbar | None = None
        self.summary_progress_active = False
        self.summary_progress_total = 0
        self.summary_progress_outcome = ""
        self.summary_runtime_profiles = self._load_summary_runtime_profiles()
        self.summary_backend_var = tk.StringVar()
        self.summary_model_var = tk.StringVar()
        self.summary_reasoning_var = tk.StringVar()
        self.summary_runtime_status_var = tk.StringVar()
        self.summary_model_notes_var = tk.StringVar()
        self.summary_reasoning_notes_var = tk.StringVar()
        self.summary_model_catalog_hint_var = tk.StringVar()
        self.current_summary_backend_key = ""
        self.summary_model_catalog_refreshing = False

        write_startup_log("Build UI begin")
        self._build_ui()
        write_startup_log("Build UI done")
        self.protocol("WM_DELETE_WINDOW", self._handle_close_request)
        write_startup_log("PaperSyncGUI init done")

    def _initial_refresh_all(self) -> None:
        write_startup_log("Initial refresh begin")
        try:
            self._refresh_all(log_refresh=False)
        finally:
            write_startup_log("Initial refresh scheduled")

    def _present_main_window(self) -> None:
        write_startup_log("Present main window begin")
        try:
            self.state("normal")
            self.deiconify()
        except tk.TclError:
            pass
        write_startup_log("Present main window end")

    def _default_summary_backend_key(self) -> str:
        return sp.default_available_backend(self.config_data)

    def _load_summary_runtime_profiles(self) -> dict[str, Any]:
        codex_defaults = load_codex_cli_defaults()
        profiles: dict[str, dict[str, str]] = {
            "openai": {
                "model": str(self.config_data.openai_model or "").strip(),
                "reasoning": str(self.config_data.openai_reasoning_effort or "medium").strip().lower() or "medium",
            },
            "codex": {
                "model": str(
                    self.config_data.codex_cli_model
                    or codex_defaults.get("model")
                    or "gpt-5.5"
                ).strip(),
                "reasoning": str(
                    self.config_data.codex_cli_reasoning_effort
                    or codex_defaults.get("reasoning")
                    or "xhigh"
                ).strip().lower()
                or "xhigh",
            },
        }
        selected_backend = self._default_summary_backend_key()

        stored = load_summary_runtime_prefs()
        if isinstance(stored, dict):
            backend_value = str(stored.get("selected_backend") or "").strip().lower()
            if backend_value in SUMMARY_BACKEND_LABELS:
                selected_backend = backend_value
            stored_profiles = stored.get("profiles", {})
            if isinstance(stored_profiles, dict):
                for backend_key in SUMMARY_BACKEND_LABELS:
                    profile = stored_profiles.get(backend_key, {})
                    if not isinstance(profile, dict):
                        continue
                    model = str(profile.get("model") or "").strip()
                    reasoning = str(profile.get("reasoning") or "").strip().lower()
                    if model:
                        profiles[backend_key]["model"] = model
                    if reasoning in SUMMARY_REASONING_OPTIONS:
                        profiles[backend_key]["reasoning"] = reasoning

        return {
            "selected_backend": selected_backend,
            "profiles": profiles,
        }

    def _persist_summary_runtime_profiles(self) -> None:
        payload = {
            "selected_backend": self.current_summary_backend_key or self._default_summary_backend_key(),
            "profiles": self.summary_runtime_profiles.get("profiles", {}),
        }
        save_summary_runtime_prefs(payload)

    def _summary_backend_display(self, backend_key: str) -> str:
        return SUMMARY_BACKEND_LABELS.get(backend_key, SUMMARY_BACKEND_LABELS["codex"])

    def _summary_backend_key_from_display(self, display_value: str) -> str:
        for key, label in SUMMARY_BACKEND_LABELS.items():
            if label == display_value:
                return key
        return self.current_summary_backend_key or self._default_summary_backend_key()

    def _summary_model_options_for_backend(self, backend_key: str) -> list[str]:
        options = list(SUMMARY_MODEL_OPTIONS.get(backend_key, ()))
        custom_model = ""
        profiles = self.summary_runtime_profiles.get("profiles", {})
        if isinstance(profiles, dict):
            profile = profiles.get(backend_key, {})
            if isinstance(profile, dict):
                custom_model = str(profile.get("model") or "").strip()
        current_model = str(self.summary_model_var.get() or "").strip()
        for candidate in (custom_model, current_model):
            if candidate and candidate not in options:
                options.insert(0, candidate)
        return options

    def _update_summary_model_choices(self, backend_key: str) -> None:
        if not hasattr(self, "combo_summary_model"):
            return
        options = self._summary_model_options_for_backend(backend_key)
        self.combo_summary_model.configure(values=options)
        self._refresh_summary_model_catalog(backend_key, options)

    def _summary_model_detail(self, model_name: str) -> dict[str, str]:
        return SUMMARY_MODEL_DETAILS.get(
            model_name,
            {
                "profile": "自定义",
                "use": "账户或服务端配置里的自定义模型；可用性取决于当前后端。",
            },
        )

    def _refresh_summary_model_catalog(self, backend_key: str, options: list[str] | None = None) -> None:
        if not hasattr(self, "tree_summary_models"):
            return
        model_options = options if options is not None else self._summary_model_options_for_backend(backend_key)
        self.summary_model_catalog_refreshing = True
        try:
            existing_items = self.tree_summary_models.get_children()
            if existing_items:
                self.tree_summary_models.delete(*existing_items)
            selected_model = str(self.summary_model_var.get() or "").strip()
            selected_item = ""
            for model_name in model_options:
                detail = self._summary_model_detail(model_name)
                item_id = model_name
                self.tree_summary_models.insert(
                    "",
                    "end",
                    iid=item_id,
                    values=(model_name, detail["profile"], detail["use"]),
                )
                if model_name == selected_model:
                    selected_item = item_id
            if selected_item:
                self.tree_summary_models.selection_set(selected_item)
                self.tree_summary_models.focus(selected_item)
            self.summary_model_catalog_hint_var.set(SUMMARY_BACKEND_HINTS.get(backend_key, ""))
        finally:
            self.summary_model_catalog_refreshing = False

    def _on_summary_model_catalog_selected(self, _event: object | None = None) -> None:
        if self.summary_model_catalog_refreshing:
            return
        if not hasattr(self, "tree_summary_models"):
            return
        selection = self.tree_summary_models.selection()
        if not selection:
            return
        values = self.tree_summary_models.item(selection[0], "values")
        if not values:
            return
        selected_model = str(values[0])
        if selected_model == str(self.summary_model_var.get() or ""):
            return
        self.summary_model_var.set(selected_model)
        self._on_summary_runtime_field_changed()

    def _format_summary_model_notes(self, backend_key: str) -> str:
        model_name = str(self.summary_model_var.get() or "").strip()
        if not model_name:
            options = self._summary_model_options_for_backend(backend_key)
            model_name = options[0] if options else "default"
        comment = SUMMARY_MODEL_COMMENTS.get(
            model_name,
            "速度: 未知 | 推理: 未知 | 自定义模型，实际表现取决于账户可用模型和服务端配置。",
        )
        return f"Selected model: {model_name}\n{comment}"

    def _format_summary_reasoning_notes(self) -> str:
        return "Reasoning: " + "  |  ".join(
            f"{reasoning}: {SUMMARY_REASONING_COMMENTS[reasoning]}" for reasoning in SUMMARY_REASONING_OPTIONS
        )

    def _update_summary_runtime_notes(self) -> None:
        backend_key = self.current_summary_backend_key or self._default_summary_backend_key()
        self._refresh_summary_model_catalog(backend_key)
        self.summary_model_notes_var.set(self._format_summary_model_notes(backend_key))
        self.summary_reasoning_notes_var.set(self._format_summary_reasoning_notes())

    def _sync_current_summary_profile(self) -> None:
        backend_key = self.current_summary_backend_key
        if backend_key not in SUMMARY_BACKEND_LABELS:
            return
        profiles = self.summary_runtime_profiles.setdefault("profiles", {})
        profile = profiles.setdefault(backend_key, {})
        model = str(self.summary_model_var.get() or "").strip()
        reasoning = str(self.summary_reasoning_var.get() or "").strip().lower()
        if model:
            profile["model"] = model
        if reasoning in SUMMARY_REASONING_OPTIONS:
            profile["reasoning"] = reasoning

    def _apply_summary_runtime_profile(self, backend_key: str, *, persist_current: bool) -> None:
        if persist_current:
            self._sync_current_summary_profile()
        profiles = self.summary_runtime_profiles.setdefault("profiles", {})
        profile = profiles.setdefault(backend_key, {})
        self.current_summary_backend_key = backend_key
        self.summary_backend_var.set(self._summary_backend_display(backend_key))
        self._update_summary_model_choices(backend_key)
        self.summary_model_var.set(str(profile.get("model") or "").strip())
        reasoning = str(profile.get("reasoning") or "").strip().lower()
        self.summary_reasoning_var.set(reasoning if reasoning in SUMMARY_REASONING_OPTIONS else "medium")
        self.summary_runtime_profiles["selected_backend"] = backend_key
        if persist_current:
            self._persist_summary_runtime_profiles()
        self._update_summary_runtime_status()
        self._update_summary_runtime_notes()

    def _on_summary_backend_changed(self, _event: object | None = None) -> None:
        backend_key = self._summary_backend_key_from_display(self.summary_backend_var.get())
        self._apply_summary_runtime_profile(backend_key, persist_current=True)

    def _on_summary_runtime_field_changed(self, _event: object | None = None) -> None:
        self._sync_current_summary_profile()
        self._persist_summary_runtime_profiles()
        self._update_summary_runtime_status()
        self._update_summary_model_choices(self.current_summary_backend_key or self._default_summary_backend_key())
        self._update_summary_runtime_notes()

    def _reset_summary_runtime_defaults(self) -> None:
        SUMMARY_RUNTIME_PREFS_PATH.unlink(missing_ok=True)
        self.summary_runtime_profiles = self._load_summary_runtime_profiles()
        backend_key = str(self.summary_runtime_profiles.get("selected_backend") or self._default_summary_backend_key())
        self._apply_summary_runtime_profile(backend_key, persist_current=False)

    def _selected_summary_runtime(self) -> tuple[str, str, str]:
        backend_key = self.current_summary_backend_key or self._default_summary_backend_key()
        model = str(self.summary_model_var.get() or "").strip()
        reasoning = str(self.summary_reasoning_var.get() or "").strip().lower()
        if reasoning not in SUMMARY_REASONING_OPTIONS:
            reasoning = "medium" if backend_key in {"openai", "openai_compatible"} else "xhigh"
        return backend_key, model, reasoning

    def _update_summary_runtime_status(self) -> None:
        backend_key, model, reasoning = self._selected_summary_runtime()
        availability_note = ""
        if backend_key == "openai" and not sp.has_openai_api_key(self.config_data):
            availability_note = " | OPENAI_API_KEY missing"
        if backend_key == "openai_compatible" and not sp.has_compatible_api_key(self.config_data):
            availability_note = f" | {self.config_data.compatible_api_key_env} missing"
        if backend_key == "codex" and not sp.codex_cli_available(self.config_data):
            availability_note = " | codex CLI unavailable"
        self.summary_runtime_status_var.set(
            f"Current: {self._summary_backend_display(backend_key)} | "
            f"Model: {model or 'default'} | Reasoning: {reasoning}{availability_note}"
        )

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=10, pady=(10, 6))

        self.btn_refresh = ttk.Button(toolbar, text="Refresh / 刷新", command=lambda: self._refresh_all(log_refresh=True))
        self.btn_refresh.pack(side="left")

        self.btn_open_paper = ttk.Button(
            toolbar,
            text="Open 01-paper",
            command=lambda: open_path(self.config_data.paper_dir),
        )
        self.btn_open_paper.pack(side="left", padx=(8, 0))

        self.btn_open_spec = ttk.Button(
            toolbar,
            text="Open default.md",
            command=lambda: open_path(self.config_data.spec_path),
        )
        self.btn_open_spec.pack(side="left", padx=(8, 0))

        self.btn_open_guide = ttk.Button(
            toolbar,
            text="Open Guide",
            command=lambda: open_path(SCRIPT_DIR / "GUI_WORKFLOW.md"),
        )
        self.btn_open_guide.pack(side="left", padx=(8, 0))

        self.btn_open_vault = ttk.Button(
            toolbar,
            text="Open Obsidian Vault",
            command=lambda: open_path(self.config_data.obsidian_vault_dir),
        )
        self.btn_open_vault.pack(side="left", padx=(8, 0))

        self.btn_sync_changed = ttk.Button(
            toolbar,
            text="Sync Changed / 增量同步",
            command=lambda: self._run_sync_existing_packages(mode="changed"),
        )
        self.btn_sync_changed.pack(side="left", padx=(8, 0))

        self.btn_sync_full = ttk.Button(
            toolbar,
            text="Check All / 全量核对",
            command=lambda: self._run_sync_existing_packages(mode="full"),
        )
        self.btn_sync_full.pack(side="left", padx=(8, 0))

        self.btn_stop = ttk.Button(toolbar, text="Stop Task", command=self._stop_active_task, state="disabled")
        self.btn_stop.pack(side="right")

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=10, pady=(0, 6))
        self.label_collection = ttk.Label(status_frame, text="Zotero current collection: (checking...)")
        self.label_collection.pack(side="left")
        self.label_task = ttk.Label(status_frame, text="Task: Idle")
        self.label_task.pack(side="left", padx=(18, 0))
        self.label_refresh = ttk.Label(status_frame, text="Last refresh: -")
        self.label_refresh.pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        pdf_tab = ttk.Frame(notebook)
        notebook.add(pdf_tab, text="01-paper Monitor")
        collection_tab = ttk.Frame(notebook)
        notebook.add(collection_tab, text="01-paper-sync Collection")

        pdf_pane = ttk.Panedwindow(pdf_tab, orient="horizontal")
        pdf_pane.pack(fill="both", expand=True, padx=4, pady=4)

        new_frame = ttk.Labelframe(pdf_pane, text="New This Session / 本次新增")
        pdf_pane.add(new_frame, weight=1)
        self.tree_new = self._make_pdf_tree(new_frame)

        unsynced_frame = ttk.Labelframe(pdf_pane, text="Not In Zotero / 尚未同步")
        pdf_pane.add(unsynced_frame, weight=1)
        self.tree_unsynced = self._make_pdf_tree(unsynced_frame)

        pdf_actions = ttk.Frame(pdf_tab)
        pdf_actions.pack(fill="x", padx=4, pady=(0, 4))
        self.btn_import = ttk.Button(pdf_actions, text="Add To Zotero / 添加至 Zotero", command=self._import_selected_pdfs)
        self.btn_import.pack(side="left")
        self.btn_open_selected_pdf = ttk.Button(
            pdf_actions,
            text="Open Selected PDF",
            command=self._open_selected_pdf_from_pdf_tab,
        )
        self.btn_open_selected_pdf.pack(side="left", padx=(8, 0))

        collection_frame = ttk.Frame(collection_tab)
        collection_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree_collection = self._make_collection_tree(collection_frame)

        runtime_frame = ttk.Labelframe(collection_tab, text="Summary Runtime / 总结模型设置")
        runtime_frame.pack(fill="x", padx=4, pady=(0, 4))

        ttk.Label(runtime_frame, text="Backend / 后端").grid(row=0, column=0, padx=(8, 6), pady=6, sticky="w")
        self.combo_summary_backend = ttk.Combobox(
            runtime_frame,
            state="readonly",
            width=16,
            textvariable=self.summary_backend_var,
            values=[
                SUMMARY_BACKEND_LABELS["openai"],
                SUMMARY_BACKEND_LABELS["openai_compatible"],
                SUMMARY_BACKEND_LABELS["codex"],
            ],
        )
        self.combo_summary_backend.grid(row=0, column=1, padx=(0, 10), pady=6, sticky="w")
        self.combo_summary_backend.bind("<<ComboboxSelected>>", self._on_summary_backend_changed)

        ttk.Label(runtime_frame, text="Model / 模型").grid(row=0, column=2, padx=(0, 6), pady=6, sticky="w")
        self.combo_summary_model = ttk.Combobox(
            runtime_frame,
            width=18,
            textvariable=self.summary_model_var,
        )
        self.combo_summary_model.grid(row=0, column=3, padx=(0, 10), pady=6, sticky="we")
        self.combo_summary_model.bind("<<ComboboxSelected>>", self._on_summary_runtime_field_changed)
        self.combo_summary_model.bind("<FocusOut>", self._on_summary_runtime_field_changed)
        self.combo_summary_model.bind("<Return>", self._on_summary_runtime_field_changed)

        ttk.Label(runtime_frame, text="Reasoning / 推理").grid(row=0, column=4, padx=(0, 6), pady=6, sticky="w")
        self.combo_summary_reasoning = ttk.Combobox(
            runtime_frame,
            state="readonly",
            width=10,
            textvariable=self.summary_reasoning_var,
            values=list(SUMMARY_REASONING_OPTIONS),
        )
        self.combo_summary_reasoning.grid(row=0, column=5, padx=(0, 10), pady=6, sticky="w")
        self.combo_summary_reasoning.bind("<<ComboboxSelected>>", self._on_summary_runtime_field_changed)

        self.btn_reset_summary_runtime = ttk.Button(
            runtime_frame,
            text="Reset / 默认",
            command=self._reset_summary_runtime_defaults,
        )
        self.btn_reset_summary_runtime.grid(row=0, column=6, padx=(0, 8), pady=6, sticky="w")

        self.label_summary_runtime = ttk.Label(
            runtime_frame,
            textvariable=self.summary_runtime_status_var,
        )
        self.label_summary_runtime.grid(row=1, column=0, columnspan=7, padx=8, pady=(0, 6), sticky="w")

        self.tree_summary_models = ttk.Treeview(
            runtime_frame,
            columns=("model", "profile", "use"),
            show="headings",
            height=5,
            selectmode="browse",
        )
        self.tree_summary_models.heading("model", text="Model / 模型")
        self.tree_summary_models.heading("profile", text="Profile / 档位")
        self.tree_summary_models.heading("use", text="Best Use / 用途")
        self.tree_summary_models.column("model", width=150, anchor="w", stretch=False)
        self.tree_summary_models.column("profile", width=110, anchor="w", stretch=False)
        self.tree_summary_models.column("use", width=760, anchor="w", stretch=True)
        self.tree_summary_models.grid(row=2, column=0, columnspan=7, padx=8, pady=(0, 4), sticky="we")
        self.tree_summary_models.bind("<<TreeviewSelect>>", self._on_summary_model_catalog_selected)

        self.label_summary_catalog_hint = ttk.Label(
            runtime_frame,
            textvariable=self.summary_model_catalog_hint_var,
            justify="left",
            anchor="w",
            wraplength=1100,
        )
        self.label_summary_catalog_hint.grid(row=3, column=0, columnspan=7, padx=8, pady=(0, 4), sticky="we")

        self.label_summary_model_notes = ttk.Label(
            runtime_frame,
            textvariable=self.summary_model_notes_var,
            justify="left",
            anchor="w",
            wraplength=1100,
        )
        self.label_summary_model_notes.grid(row=4, column=0, columnspan=7, padx=8, pady=(0, 4), sticky="we")
        self.label_summary_reasoning_notes = ttk.Label(
            runtime_frame,
            textvariable=self.summary_reasoning_notes_var,
            justify="left",
            anchor="w",
            wraplength=1100,
        )
        self.label_summary_reasoning_notes.grid(row=5, column=0, columnspan=7, padx=8, pady=(0, 6), sticky="we")
        runtime_frame.columnconfigure(3, weight=1)

        collection_actions = ttk.Frame(collection_tab)
        collection_actions.pack(fill="x", padx=4, pady=(0, 4))
        self.btn_summarize = ttk.Button(
            collection_actions,
            text="Generate / Attach Summary",
            command=self._summarize_selected_items,
        )
        self.btn_summarize.pack(side="left")
        self.btn_compare = ttk.Button(
            collection_actions,
            text="Compare / 对比总结",
            command=self._compare_selected_items,
        )
        self.btn_compare.pack(side="left", padx=(8, 0))
        self.btn_open_selected_item_pdf = ttk.Button(
            collection_actions,
            text="Open Selected PDF",
            command=self._open_selected_collection_pdf,
        )
        self.btn_open_selected_item_pdf.pack(side="left", padx=(8, 0))
        self.btn_open_output = ttk.Button(
            collection_actions,
            text="Open Output",
            command=self._open_selected_output,
        )
        self.btn_open_output.pack(side="left", padx=(8, 0))

        log_frame = ttk.Labelframe(self, text="Live Log / 实时输出")
        log_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.log_text = tk.Text(log_frame, height=16, wrap="none", font=("Consolas", 10))
        y_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        x_scroll = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        initial_backend = str(
            self.summary_runtime_profiles.get("selected_backend") or self._default_summary_backend_key()
        ).strip().lower()
        if initial_backend not in SUMMARY_BACKEND_LABELS:
            initial_backend = self._default_summary_backend_key()
        self._apply_summary_runtime_profile(initial_backend, persist_current=False)

    def _make_pdf_tree(self, parent: ttk.Widget) -> ttk.Treeview:
        columns = ("name", "modified", "size")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        tree.heading("name", text="PDF")
        tree.heading("modified", text="Modified")
        tree.heading("size", text="Size MB")
        tree.column("name", width=420, anchor="w")
        tree.column("modified", width=150, anchor="center")
        tree.column("size", width=80, anchor="e")
        y_scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=y_scroll.set)
        tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        y_scroll.pack(side="right", fill="y", pady=6, padx=(0, 6))
        return tree

    def _make_collection_tree(self, parent: ttk.Widget) -> ttk.Treeview:
        columns = ("date_added", "title", "year", "pdf", "summary", "tags")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        tree.heading("date_added", text="Added")
        tree.heading("title", text="Title")
        tree.heading("year", text="Year")
        tree.heading("pdf", text="PDF Source")
        tree.heading("summary", text="Summary")
        tree.heading("tags", text="Tags")
        tree.column("date_added", width=140, anchor="center")
        tree.column("title", width=420, anchor="w")
        tree.column("year", width=60, anchor="center")
        tree.column("pdf", width=260, anchor="w")
        tree.column("summary", width=90, anchor="center")
        tree.column("tags", width=300, anchor="w")
        y_scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=y_scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")
        return tree

    def _cached_file_sha256(self, path: Path) -> str:
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            return ""

        cache_key = (str(resolved), int(stat.st_size), int(stat.st_mtime_ns))
        cached = self.file_sha256_cache.get(cache_key)
        if cached is not None:
            return cached

        digest = hashlib.sha256()
        try:
            with resolved.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError:
            self.file_sha256_cache[cache_key] = ""
            return ""

        value = digest.hexdigest()
        self.file_sha256_cache[cache_key] = value
        return value

    def _safe_file_signature(self, path: Path) -> dict[str, int] | None:
        try:
            if not path.exists():
                return None
            return sp.file_signature(path)
        except OSError:
            return None

    def _has_pending_sync_changes(self) -> bool:
        for item in self.current_collection_items:
            record = item.get("state_record")
            if not isinstance(record, dict):
                return True

            preferred_pdf = sp.preferred_record_pdf_path(
                self.config_data,
                item.get("preferred_pdf"),
                record.get("pdf_path"),
            )

            comparisons = (
                (item.get("title"), record.get("title")),
                (item.get("year"), record.get("year")),
                (item.get("source"), record.get("source")),
                (item.get("doi"), record.get("doi")),
                (item.get("arxiv"), record.get("arxiv")),
                (item.get("citation_key"), record.get("citation_key")),
                (item.get("item_key"), record.get("zotero_item_key")),
                (item.get("date_added"), record.get("date_added")),
                (item.get("date_modified"), record.get("date_modified")),
                (preferred_pdf, record.get("pdf_path")),
            )
            for left, right in comparisons:
                if str(left or "") != str(right or ""):
                    return True

            if list(item.get("tags") or []) != list(record.get("tags") or []):
                return True
            if list(item.get("authors") or []) != list(record.get("authors") or []):
                return True
            if list(item.get("creators") or []) != list(record.get("creators") or []):
                return True

            summary_md = str(record.get("summary_md") or "").strip()
            package_dir = str(record.get("package_dir") or "").strip()
            if summary_md:
                summary_path = Path(summary_md)
                if not summary_path.exists():
                    return True
                if record.get("summary_md_sig") != self._safe_file_signature(summary_path):
                    return True
            if package_dir:
                obsidian_dir = Path(str(record.get("obsidian_dir") or "")) if record.get("obsidian_dir") else None
                obsidian_md = Path(str(record.get("obsidian_md") or "")) if record.get("obsidian_md") else None
                if obsidian_dir is not None and not obsidian_dir.exists():
                    return True
                if obsidian_md is not None and not obsidian_md.exists():
                    return True
                manifest_path = Path(package_dir) / "manifest.json"
                if manifest_path.exists() and record.get("manifest_sig") != self._safe_file_signature(manifest_path):
                    return True

        return False

    def _handle_close_request(self) -> None:
        if self.worker_thread is not None:
            messagebox.showinfo("Paper Sync Manager", "A task is still running. Stop it first, then close the window.")
            return

        try:
            pending_changes = self._has_pending_sync_changes()
        except Exception as exc:
            self._log(f"[close-check-error] {exc}")
            pending_changes = False

        if pending_changes:
            result = messagebox.askyesnocancel(
                "Paper Sync Manager",
                "Detected unsynced collection/package changes. Run incremental sync before closing?",
            )
            if result is None:
                return
            if result:
                self.close_after_task = True
                if not self._run_sync_existing_packages(mode="changed"):
                    self.close_after_task = False
                return

        self.destroy()

    def _open_summary_progress_dialog(self, total_jobs: int) -> None:
        self._close_summary_progress_dialog()

        window = tk.Toplevel(self)
        window.title("Summary Progress / 总结进度")
        window.geometry("980x620")
        window.minsize(760, 480)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", self._close_summary_progress_dialog)

        info_frame = ttk.Frame(window, padding=12)
        info_frame.pack(fill="x")

        docs_frame = ttk.Frame(info_frame)
        docs_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(docs_frame, text="文献进度:").pack(side="left")
        self.summary_progress_label_docs = ttk.Label(docs_frame, text=f"0 / {total_jobs}")
        self.summary_progress_label_docs.pack(side="left", padx=(8, 0))

        title_frame = ttk.Frame(info_frame)
        title_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(title_frame, text="当前文献:").pack(side="left")
        self.summary_progress_label_title = ttk.Label(title_frame, text="等待开始")
        self.summary_progress_label_title.pack(side="left", padx=(8, 0), fill="x", expand=True)

        pdf_frame = ttk.Frame(info_frame)
        pdf_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(pdf_frame, text="PDF 路径:").pack(side="left")
        self.summary_progress_label_pdf = ttk.Label(pdf_frame, text="-")
        self.summary_progress_label_pdf.pack(side="left", padx=(8, 0), fill="x", expand=True)

        stage_frame = ttk.Frame(info_frame)
        stage_frame.pack(fill="x")
        ttk.Label(stage_frame, text="当前阶段:").pack(side="left")
        self.summary_progress_label_stage = ttk.Label(stage_frame, text="等待启动")
        self.summary_progress_label_stage.pack(side="left", padx=(8, 0), fill="x", expand=True)

        detail_frame = ttk.Frame(info_frame)
        detail_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(detail_frame, text="阶段说明:").pack(side="left")
        self.summary_progress_label_detail = ttk.Label(detail_frame, text="等待启动总结流程。")
        self.summary_progress_label_detail.pack(side="left", padx=(8, 0), fill="x", expand=True)

        last_frame = ttk.Frame(info_frame)
        last_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(last_frame, text="最近输出:").pack(side="left")
        self.summary_progress_label_last = ttk.Label(last_frame, text="-")
        self.summary_progress_label_last.pack(side="left", padx=(8, 0), fill="x", expand=True)

        self.summary_progress_bar = ttk.Progressbar(window, mode="indeterminate")
        self.summary_progress_bar.pack(fill="x", padx=12, pady=(0, 10))
        self.summary_progress_bar.start(12)

        log_frame = ttk.Labelframe(window, text="实时输出", padding=(8, 6))
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.summary_progress_text = tk.Text(log_frame, wrap="none", font=("Consolas", 10))
        y_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.summary_progress_text.yview)
        x_scroll = ttk.Scrollbar(log_frame, orient="horizontal", command=self.summary_progress_text.xview)
        self.summary_progress_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.summary_progress_text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        button_frame = ttk.Frame(window, padding=(12, 0, 12, 12))
        button_frame.pack(fill="x")
        ttk.Button(button_frame, text="停止当前任务", command=self._stop_active_task).pack(side="left")
        ttk.Button(button_frame, text="关闭窗口", command=self._close_summary_progress_dialog).pack(side="right")

        self.summary_progress_window = window
        self.summary_progress_active = True
        self.summary_progress_total = total_jobs
        self.summary_progress_outcome = "running"
        window.lift()
        window.focus_force()

    def _close_summary_progress_dialog(self) -> None:
        if self.summary_progress_window is not None:
            try:
                if self.summary_progress_window.winfo_exists():
                    self.summary_progress_window.destroy()
            except tk.TclError:
                pass
        self.summary_progress_window = None
        self.summary_progress_label_docs = None
        self.summary_progress_label_title = None
        self.summary_progress_label_pdf = None
        self.summary_progress_label_stage = None
        self.summary_progress_label_detail = None
        self.summary_progress_label_last = None
        self.summary_progress_text = None
        self.summary_progress_bar = None

    def _update_summary_progress_item(self, payload: dict[str, Any]) -> None:
        if self.summary_progress_label_docs is not None:
            self.summary_progress_label_docs.configure(
                text=f"{payload.get('index', 0)} / {payload.get('total', self.summary_progress_total or 0)}"
            )
        if self.summary_progress_label_title is not None:
            self.summary_progress_label_title.configure(text=str(payload.get("title") or "Untitled"))
        if self.summary_progress_label_pdf is not None:
            self.summary_progress_label_pdf.configure(text=str(payload.get("pdf") or "-"))
        self._set_summary_progress_stage("准备处理当前文献")

    def _append_summary_progress_log(self, message: str) -> None:
        if self.summary_progress_label_last is not None:
            self.summary_progress_label_last.configure(text=message)
        if self.summary_progress_text is None:
            stage = infer_summary_stage(message)
            if stage:
                self._set_summary_progress_stage(stage)
            return
        timestamp = time.strftime("%H:%M:%S")
        self.summary_progress_text.insert("end", f"[{timestamp}] {message}\n")
        self.summary_progress_text.see("end")
        stage = infer_summary_stage(message)
        if stage:
            self._set_summary_progress_stage(stage)

    def _set_summary_progress_stage(self, value: str) -> None:
        if self.summary_progress_label_stage is not None and value:
            self.summary_progress_label_stage.configure(text=value)
        detail = describe_summary_stage(value)
        if self.summary_progress_label_detail is not None:
            self.summary_progress_label_detail.configure(text=detail or "-")

    def _finalize_summary_progress(self, value: str) -> None:
        self.summary_progress_active = False
        self.summary_progress_outcome = value
        if self.summary_progress_bar is not None:
            self.summary_progress_bar.stop()
        self._set_summary_progress_stage(value)

    def _enqueue_summary_progress_log(self, message: str) -> None:
        self.ui_queue.put(("summary-progress-log", message))

    def _set_task(self, value: str) -> None:
        self.current_task = value
        self.label_task.configure(text=f"Task: {value}")

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")

    def _enqueue_log(self, message: str) -> None:
        self.ui_queue.put(("log", message))

    def _drain_ui_queue(self, *, reschedule: bool = True) -> None:
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(str(payload))
            elif kind == "task":
                self._set_task(str(payload))
            elif kind == "refresh":
                self._refresh_all(log_refresh=False)
            elif kind == "refresh-data":
                self._apply_refresh_data(payload if isinstance(payload, dict) else {})
            elif kind == "refresh-error":
                self._log(f"[refresh-error] {payload}")
            elif kind == "refresh-finished":
                self.refresh_thread = None
            elif kind == "summary-progress-item":
                self._update_summary_progress_item(payload if isinstance(payload, dict) else {})
            elif kind == "summary-progress-log":
                self._append_summary_progress_log(str(payload))
            elif kind == "summary-progress-stage":
                self._set_summary_progress_stage(str(payload))
            elif kind == "task-finished":
                self._finish_background_task()
            elif kind == "error-dialog":
                messagebox.showerror("Paper Sync Manager", str(payload))
        if reschedule:
            self.after(300, self._drain_ui_queue)

    def _periodic_refresh(self) -> None:
        if self.worker_thread is None and self.refresh_thread is None:
            self._refresh_all(log_refresh=False)
        self.after(REFRESH_INTERVAL_MS, self._periodic_refresh)

    def _scan_pdfs(self) -> list[Path]:
        if not self.config_data.paper_dir.exists():
            return []
        return sorted(self.config_data.paper_dir.rglob("*.pdf"))

    def _refresh_all(self, *, log_refresh: bool) -> None:
        if self.refresh_thread is not None and self.refresh_thread.is_alive():
            return

        def worker() -> None:
            try:
                current_pdfs = self._scan_pdfs()
                current_pdf_set = {str(path.resolve()) for path in current_pdfs}
                collection_name = ""
                collection_error = ""
                try:
                    selected = connector_get_selected_collection()
                    collection_name = str(selected.get("name") or "")
                except Exception as exc:
                    collection_error = str(exc)

                collection_items = load_collection_items(self.config_data, current_pdfs)
                self.ui_queue.put(
                    (
                        "refresh-data",
                        {
                            "current_pdfs": current_pdfs,
                            "current_pdf_set": current_pdf_set,
                            "collection_name": collection_name,
                            "collection_error": collection_error,
                            "collection_items": collection_items,
                            "log_refresh": log_refresh,
                        },
                    )
                )
            except Exception as exc:
                self.ui_queue.put(("refresh-error", exc))
            finally:
                self.ui_queue.put(("refresh-finished", None))

        self.label_refresh.configure(text="Last refresh: running...")
        self.refresh_thread = threading.Thread(target=worker, name="paper-sync-refresh", daemon=True)
        self.refresh_thread.start()

    def _apply_refresh_data(self, payload: dict[str, Any]) -> None:
        try:
            self.current_pdfs = list(payload.get("current_pdfs") or [])
            current_pdf_set = set(payload.get("current_pdf_set") or set())
            if self.initial_pdf_set is None:
                self.initial_pdf_set = set(current_pdf_set)

            collection_error = str(payload.get("collection_error") or "")
            if collection_error:
                self.current_collection_status = ""
                self.label_collection.configure(text=f"Zotero current collection: unavailable ({collection_error})")
            else:
                collection_name = str(payload.get("collection_name") or "")
                self.current_collection_status = collection_name
                self.label_collection.configure(
                    text=f"Zotero current collection: {collection_name or '(unknown)'}"
                )

            self.current_collection_items = list(payload.get("collection_items") or [])
            self._populate_pdf_trees(current_pdf_set)
            self._populate_collection_tree()
            self.label_refresh.configure(text=f"Last refresh: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            if bool(payload.get("log_refresh")):
                self._log(
                    f"Refresh complete: {len(self.current_pdfs)} PDFs in 01-paper, "
                    f"{len(self.current_collection_items)} items in {self.config_data.zotero_local_collection_name}."
                )
        except Exception as exc:
            self._log(f"[refresh-error] {exc}")

    def _populate_pdf_trees(self, current_pdf_set: set[str]) -> None:
        new_paths = sorted(Path(path) for path in current_pdf_set - (self.initial_pdf_set or set()))
        synced_abs_paths = set()
        synced_names = {
            attachment["basename"].lower()
            for item in self.current_collection_items
            for attachment in item.get("pdf_attachments", [])
            if attachment.get("basename")
        }
        for item in self.current_collection_items:
            for attachment in item.get("pdf_attachments", []):
                abs_path = str(attachment.get("abs_path") or "").strip()
                if not abs_path:
                    continue
                try:
                    synced_abs_paths.add(str(Path(abs_path).resolve()).lower())
                except OSError:
                    continue

        unmatched_candidates: list[Path] = []
        for path in self.current_pdfs:
            resolved_key = str(path.resolve()).lower()
            if resolved_key in synced_abs_paths:
                continue
            if path.name.lower() in synced_names:
                continue
            unmatched_candidates.append(path)

        attachment_hashes: set[str] = set()
        if unmatched_candidates:
            for item in self.current_collection_items:
                for attachment in item.get("pdf_attachments", []):
                    abs_path = str(attachment.get("abs_path") or "").strip()
                    if not abs_path:
                        continue
                    attachment_path = Path(abs_path)
                    if not attachment_path.exists():
                        continue
                    digest = self._cached_file_sha256(attachment_path)
                    if digest:
                        attachment_hashes.add(digest)

        unsynced_paths: list[Path] = []
        for path in unmatched_candidates:
            digest = self._cached_file_sha256(path)
            if digest and digest in attachment_hashes:
                continue
            unsynced_paths.append(path)

        for tree in (self.tree_new, self.tree_unsynced):
            tree.delete(*tree.get_children())

        self.tree_new_rows.clear()
        for path in new_paths:
            iid = str(path.resolve())
            self.tree_new_rows[iid] = path
            stat = path.stat()
            self.tree_new.insert(
                "",
                "end",
                iid=iid,
                values=(
                    str(path.relative_to(self.config_data.workspace_root)),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    f"{stat.st_size / 1024 / 1024:.2f}",
                ),
            )

        self.tree_unsynced_rows.clear()
        for path in unsynced_paths:
            iid = str(path.resolve())
            self.tree_unsynced_rows[iid] = path
            stat = path.stat()
            self.tree_unsynced.insert(
                "",
                "end",
                iid=iid,
                values=(
                    str(path.relative_to(self.config_data.workspace_root)),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    f"{stat.st_size / 1024 / 1024:.2f}",
                ),
            )

    def _populate_collection_tree(self) -> None:
        self.tree_collection.delete(*self.tree_collection.get_children())
        self.tree_collection_rows.clear()
        for item in self.current_collection_items:
            iid = item["item_key"]
            self.tree_collection_rows[iid] = item
            summary_state = "linked" if item.get("summary_attached") else "missing"
            if item.get("state_record") and Path(item["state_record"].get("summary_md", "")).exists():
                summary_state = "local"
            tags = ", ".join(item.get("tags", [])[:6])
            self.tree_collection.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("date_added", ""),
                    item.get("title", ""),
                    item.get("year", ""),
                    item.get("preferred_pdf_label", ""),
                    summary_state,
                    tags,
                ),
            )

    def _selected_pdf_paths(self) -> list[Path]:
        selected: list[Path] = []
        for iid in self.tree_new.selection():
            path = self.tree_new_rows.get(iid)
            if path is not None:
                selected.append(path)
        for iid in self.tree_unsynced.selection():
            path = self.tree_unsynced_rows.get(iid)
            if path is not None:
                selected.append(path)
        deduped = []
        seen = set()
        for path in selected:
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            deduped.append(path)
        return deduped

    def _selected_collection_items(self) -> list[dict[str, Any]]:
        return [
            self.tree_collection_rows[iid]
            for iid in self.tree_collection.selection()
            if iid in self.tree_collection_rows
        ]

    def _require_idle(self) -> bool:
        if self.worker_thread is not None:
            messagebox.showinfo("Paper Sync Manager", "A task is already running.")
            return False
        return True

    def _start_background_task(self, label: str, target: callable) -> bool:
        if not self._require_idle():
            return False
        self.stop_requested.clear()
        self.background_task_failed = False
        self.worker_thread = threading.Thread(target=target, name=label, daemon=True)
        self.btn_stop.configure(state="normal")
        self._set_task(label)
        self.worker_thread.start()
        return True

    def _finish_background_task(self) -> None:
        if self.summary_progress_active:
            if self.stop_requested.is_set():
                self._finalize_summary_progress("已停止")
            elif self.summary_progress_outcome == "处理失败":
                self._finalize_summary_progress("处理失败")
            else:
                self._finalize_summary_progress("已完成")
        self.worker_thread = None
        self.active_process = None
        self.stop_requested.clear()
        self.btn_stop.configure(state="disabled")
        self._set_task("Idle")
        should_close = self.close_after_task and not self.background_task_failed
        self.close_after_task = False
        if should_close:
            self.after(50, self.destroy)

    def _stop_active_task(self) -> None:
        if self.worker_thread is None:
            return
        self.stop_requested.set()
        if self.active_process and self.active_process.poll() is None:
            try:
                self.active_process.terminate()
            except Exception:
                pass
        self._log("Stop requested.")

    def _import_selected_pdfs(self) -> None:
        selected_paths = self._selected_pdf_paths()
        if not selected_paths:
            messagebox.showinfo("Paper Sync Manager", "Select one or more PDFs first.")
            return

        def worker() -> None:
            try:
                if not connector_ping():
                    raise RuntimeError("Zotero local connector is not reachable. Open Zotero first.")
                expected_name = self.config_data.zotero_local_collection_name
                self._enqueue_log(f"Ensuring Zotero collection: {expected_name}")
                ensure_selected_collection(self.config_data, expected_name)
                selected = connector_get_selected_collection()
                current_name = str(selected.get("name") or "")
                self._enqueue_log(f"Import target confirmed: {current_name}")
                for path in selected_paths:
                    if self.stop_requested.is_set():
                        self._enqueue_log("Import stopped by user.")
                        break
                    self._enqueue_log(f"[import] {path.name}")
                    result = import_pdf_via_connector(path)
                    recognized = result.get("recognized")
                    if recognized:
                        self._enqueue_log(
                            f"[recognized] {path.name} -> {recognized.get('title', '')}"
                        )
                    elif result.get("can_recognize"):
                        self._enqueue_log(f"[recognized] {path.name} -> no metadata match")
                    else:
                        self._enqueue_log(f"[imported] {path.name} (recognition not available)")
                self.ui_queue.put(("refresh", None))
            except Exception as exc:
                self.ui_queue.put(("error-dialog", exc))
                self._enqueue_log(f"[import-error] {exc}")
            finally:
                self.ui_queue.put(("task-finished", None))

        self._start_background_task("Import PDFs", worker)

    def _run_sync_existing_packages(self, *, mode: str = "full") -> bool:
        def worker() -> None:
            try:
                cmd = [
                    str(self.config_data.tools_python),
                    str(SCRIPT_DIR / "sync_existing_packages.py"),
                    "--config",
                    str(SCRIPT_DIR / "config.json"),
                    "--mode",
                    mode,
                ]
                self._run_subprocess_streaming(cmd, f"sync-existing-{mode}")
                self.ui_queue.put(("refresh", None))
            except Exception as exc:
                self.background_task_failed = True
                self.ui_queue.put(("error-dialog", exc))
                self._enqueue_log(f"[sync-existing-error] {exc}")
            finally:
                self.ui_queue.put(("task-finished", None))

        label = "Sync Changed To Obsidian" if mode == "changed" else "Check All Collection Sync"
        return self._start_background_task(label, worker)

    def _ask_summary_user_request(self, title: str) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("720x420")
        ttk.Label(
            dialog,
            text="输入本次特定总结要求、关注点或关键问题；可留空。",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 6))
        text = tk.Text(dialog, height=14, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        result: dict[str, str | None] = {"value": None}

        def submit() -> None:
            result["value"] = text.get("1.0", "end").strip()
            dialog.destroy()

        def cancel() -> None:
            result["value"] = None
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(buttons, text="OK / 开始", command=submit).pack(side="right")
        ttk.Button(buttons, text="Cancel / 取消", command=cancel).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        text.focus_set()
        self.wait_window(dialog)
        return result["value"]

    def _summarize_selected_items(self) -> None:
        selected_items = self._selected_collection_items()
        if not selected_items:
            messagebox.showinfo("Paper Sync Manager", "Select one or more collection items first.")
            return
        summary_user_request = self._ask_summary_user_request("Summary requirements / 总结要求")
        if summary_user_request is None:
            return

        self._sync_current_summary_profile()
        self._persist_summary_runtime_profiles()
        backend_key, model_name, reasoning_effort = self._selected_summary_runtime()
        if backend_key == "openai" and not sp.has_openai_api_key(self.config_data):
            messagebox.showerror(
                "Paper Sync Manager",
                f"OpenAI API is selected, but {self.config_data.openai_api_key_env} is not set.",
            )
            return
        if backend_key == "openai_compatible" and not sp.has_compatible_api_key(self.config_data):
            messagebox.showerror(
                "Paper Sync Manager",
                f"OpenAI-compatible API is selected, but {self.config_data.compatible_api_key_env} is not set.",
            )
            return
        if backend_key == "codex" and not sp.codex_cli_available(self.config_data):
            messagebox.showerror(
                "Paper Sync Manager",
                "Codex CLI is selected, but no usable codex executable was found in config, PATH, or installed VS Code extensions.",
            )
            return

        pdf_jobs: list[tuple[dict[str, Any], Path]] = []
        for item in selected_items:
            preferred_pdf = item.get("preferred_pdf", "")
            if not preferred_pdf:
                self._log(f"[skip] {item.get('title')} has no resolvable PDF path.")
                continue
            pdf_jobs.append((item, Path(preferred_pdf)))

        if not pdf_jobs:
            messagebox.showinfo("Paper Sync Manager", "No resolvable PDF path found for the selected items.")
            return

        def worker() -> None:
            try:
                for index, (item, pdf_path) in enumerate(pdf_jobs, start=1):
                    if self.stop_requested.is_set():
                        self._enqueue_log("Summary task stopped by user.")
                        self._enqueue_summary_progress_log("Summary task stopped by user.")
                        break
                    self.ui_queue.put(
                        (
                            "summary-progress-item",
                            {
                                "index": index,
                                "total": len(pdf_jobs),
                                "title": str(item.get("title") or "Untitled"),
                                "pdf": str(pdf_path),
                            },
                        )
                    )
                    summary_task_message = f"[summary-task] {item.get('title')} <- {pdf_path}"
                    self._enqueue_log(summary_task_message)
                    self._enqueue_summary_progress_log(summary_task_message)
                    runtime_message = (
                        f"[model] selected_backend={backend_key} "
                        f"model={model_name or 'default'} reasoning={reasoning_effort}"
                    )
                    self._enqueue_log(runtime_message)
                    self._enqueue_summary_progress_log(runtime_message)
                    cmd = [
                        str(self.config_data.tools_python),
                        str(SCRIPT_DIR / "sync_pipeline.py"),
                        "once",
                        "--config",
                        str(SCRIPT_DIR / "config.json"),
                        "--summary-backend",
                        backend_key,
                        "--summary-model",
                        model_name,
                        "--summary-reasoning-effort",
                        reasoning_effort,
                        "--summary-user-request",
                        summary_user_request,
                        "--pdf",
                        str(pdf_path),
                    ]
                    self._run_subprocess_streaming(cmd, pdf_path.name)
                self.ui_queue.put(("refresh", None))
            except Exception as exc:
                self.background_task_failed = True
                self.ui_queue.put(("error-dialog", exc))
                self._enqueue_log(f"[summary-error] {exc}")
            finally:
                self.ui_queue.put(("task-finished", None))

        self._open_summary_progress_dialog(len(pdf_jobs))
        if not self._start_background_task("Generate / Attach Summary", worker):
            self._close_summary_progress_dialog()

    def _compare_selected_items(self) -> None:
        selected_items = self._selected_collection_items()
        if len(selected_items) < 2:
            messagebox.showinfo("Paper Sync Manager", "Select at least two collection items first.")
            return
        summary_user_request = self._ask_summary_user_request("Comparison requirements / 对比总结要求")
        if summary_user_request is None:
            return

        self._sync_current_summary_profile()
        self._persist_summary_runtime_profiles()
        backend_key, model_name, reasoning_effort = self._selected_summary_runtime()
        if backend_key == "openai" and not sp.has_openai_api_key(self.config_data):
            messagebox.showerror(
                "Paper Sync Manager",
                f"OpenAI API is selected, but {self.config_data.openai_api_key_env} is not set.",
            )
            return
        if backend_key == "openai_compatible" and not sp.has_compatible_api_key(self.config_data):
            messagebox.showerror(
                "Paper Sync Manager",
                f"OpenAI-compatible API is selected, but {self.config_data.compatible_api_key_env} is not set.",
            )
            return
        if backend_key == "codex" and not sp.codex_cli_available(self.config_data):
            messagebox.showerror(
                "Paper Sync Manager",
                "Codex CLI is selected, but no usable codex executable was found in config, PATH, or installed VS Code extensions.",
            )
            return

        pdf_paths: list[Path] = []
        for item in selected_items:
            preferred_pdf = item.get("preferred_pdf", "")
            if preferred_pdf:
                pdf_paths.append(Path(preferred_pdf))
            else:
                self._log(f"[skip] {item.get('title')} has no resolvable PDF path.")
        if len(pdf_paths) < 2:
            messagebox.showinfo("Paper Sync Manager", "At least two selected items need resolvable PDF paths.")
            return

        def worker() -> None:
            try:
                self.ui_queue.put(
                    (
                        "summary-progress-item",
                        {
                            "index": 1,
                            "total": 1,
                            "title": f"Compare {len(pdf_paths)} papers",
                            "pdf": "; ".join(path.name for path in pdf_paths),
                        },
                    )
                )
                runtime_message = (
                    f"[model] selected_backend={backend_key} "
                    f"model={model_name or 'default'} reasoning={reasoning_effort}"
                )
                self._enqueue_log(runtime_message)
                self._enqueue_summary_progress_log(runtime_message)
                cmd = [
                    str(self.config_data.tools_python),
                    str(SCRIPT_DIR / "sync_pipeline.py"),
                    "compare",
                    "--config",
                    str(SCRIPT_DIR / "config.json"),
                    "--summary-backend",
                    backend_key,
                    "--summary-model",
                    model_name,
                    "--summary-reasoning-effort",
                    reasoning_effort,
                    "--summary-user-request",
                    summary_user_request,
                    "--pdf",
                    *[str(path) for path in pdf_paths],
                ]
                self._run_subprocess_streaming(cmd, "compare-selected")
                self.ui_queue.put(("refresh", None))
            except Exception as exc:
                self.background_task_failed = True
                self.ui_queue.put(("error-dialog", exc))
                self._enqueue_log(f"[compare-error] {exc}")
            finally:
                self.ui_queue.put(("task-finished", None))

        self._open_summary_progress_dialog(1)
        if not self._start_background_task("Compare Papers", worker):
            self._close_summary_progress_dialog()

    def _run_subprocess_streaming(self, cmd: list[str], label: str) -> None:
        run_message = f"[run] {' '.join(cmd)}"
        self._enqueue_log(run_message)
        if self.summary_progress_active:
            self._enqueue_summary_progress_log(run_message)
        process = subprocess.Popen(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.active_process = process
        assert process.stdout is not None
        for line in process.stdout:
            if self.stop_requested.is_set() and process.poll() is None:
                process.terminate()
            line = line.rstrip()
            if line:
                self._enqueue_log(line)
                if self.summary_progress_active:
                    self._enqueue_summary_progress_log(line)
        return_code = process.wait()
        self.active_process = None
        if return_code != 0 and not self.stop_requested.is_set():
            self.background_task_failed = True
            if self.summary_progress_active:
                self.summary_progress_outcome = "处理失败"
                self.ui_queue.put(("summary-progress-stage", "处理失败"))
            raise RuntimeError(f"Task '{label}' failed with exit code {return_code}")

    def _open_selected_pdf_from_pdf_tab(self) -> None:
        selected_paths = self._selected_pdf_paths()
        if not selected_paths:
            return
        open_path(selected_paths[0])

    def _open_selected_collection_pdf(self) -> None:
        selected = self._selected_collection_items()
        if not selected:
            return
        preferred_pdf = selected[0].get("preferred_pdf", "")
        if preferred_pdf and Path(preferred_pdf).exists():
            open_path(Path(preferred_pdf))
            return
        messagebox.showinfo("Paper Sync Manager", "No PDF path found for the selected item.")

    def _open_selected_output(self) -> None:
        selected = self._selected_collection_items()
        if not selected:
            return
        item = selected[0]
        state_record = item.get("state_record")
        if isinstance(state_record, dict):
            package_dir = state_record.get("package_dir", "")
            summary_md = state_record.get("summary_md", "")
            if package_dir and Path(package_dir).exists():
                open_path(Path(package_dir))
                return
            if summary_md and Path(summary_md).exists():
                open_path(Path(summary_md))
                return
        summary_children = item.get("summary_children", [])
        for child in summary_children:
            path_value = child.get("path", "")
            if path_value and Path(path_value).exists():
                open_path(Path(path_value))
                return
        messagebox.showinfo("Paper Sync Manager", "No local summary output found for the selected item.")


def main() -> int:
    try:
        write_startup_log("main begin")
        app = PaperSyncGUI()
        app.after(0, app._present_main_window)
        app.after(100, app._initial_refresh_all)
        app.after(300, app._drain_ui_queue)
        app.after(REFRESH_INTERVAL_MS, app._periodic_refresh)
        write_startup_log("event loop begin")
        app.mainloop()
        write_startup_log("event loop end")
        return 0
    except Exception as exc:
        write_startup_log(f"fatal: {type(exc).__name__}: {exc}")
        show_fatal_error(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
