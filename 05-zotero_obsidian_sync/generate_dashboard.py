"""Generate 00-Dashboard.md from current sync_state.json."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def esc(t: str) -> str:
    return t.replace("|", "\\|").replace("\n", " ")


def pdf_file_uri(pdf_path: str) -> str:
    return "file:///" + pdf_path.replace("\\", "/").replace(" ", "%20")


DATAVIEWJS_BLOCK = r'''const pages = dv.pages('"papers"').where(p => p.codex_sync === true);
const container = dv.container;

// --- Collect unique tags ---
const allTags = new Set();
for (const p of pages) {
  for (const t of (p.zotero_tags_original || [])) {
    if (!String(t).startsWith("/")) allTags.add(String(t));
  }
}
const tagList = [...allTags].sort();

// --- Controls ---
const ctrl = container.createEl("div", {attr:{style:"display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center;"}});
const searchInput = ctrl.createEl("input", {
  type: "text", placeholder: "\uD83D\uDD0D 搜索标题/作者/关键词\u2026",
  attr:{style:"flex:1;min-width:200px;padding:6px 10px;border:1px solid var(--background-modifier-border);border-radius:6px;background:var(--background-primary);color:var(--text-normal);font-size:14px;"}
});
const tagSelect = ctrl.createEl("select", {
  attr:{style:"padding:6px 10px;border:1px solid var(--background-modifier-border);border-radius:6px;background:var(--background-primary);color:var(--text-normal);font-size:14px;"}
});
tagSelect.createEl("option", {text: "全部标签", attr:{value:""}});
for (const tag of tagList) tagSelect.createEl("option", {text: tag, attr:{value: tag}});

// --- Sort state ---
let sortCol = "date_added", sortAsc = false;

function getVal(p, col) {
  if (col === "first_author") return String((p.authors || [])[0] || "");
  if (col === "tags_display") return (p.zotero_tags_original || []).filter(t => !String(t).startsWith("/")).join(", ");
  return String(p[col] || "");
}

function render() {
  const old = container.querySelector(".dvjs-wrap");
  if (old) old.remove();
  const ft = (searchInput.value || "").toLowerCase();
  const selTag = tagSelect.value;
  let data = [...pages];
  if (ft) data = data.filter(p => [p.title, ...(p.authors||[]), ...(p.zotero_tags_original||[]), p.source||""].join(" ").toLowerCase().includes(ft));
  if (selTag) data = data.filter(p => (p.zotero_tags_original||[]).map(String).includes(selTag));
  data.sort((a, b) => { let c = getVal(a,sortCol).localeCompare(getVal(b,sortCol),undefined,{numeric:true}); return sortAsc ? c : -c; });

  const wrap = container.createEl("div", {cls:"dvjs-wrap", attr:{style:"overflow-x:auto;"}});
  const tbl = wrap.createEl("table", {attr:{style:"width:100%;border-collapse:collapse;font-size:14px;"}});

  const cols = [
    {l:"#",c:null,w:"30px"}, {l:"标题 (PDF)",c:"title"}, {l:"AI总结",c:null,w:"60px"},
    {l:"第一作者",c:"first_author"}, {l:"年份",c:"year",w:"50px"}, {l:"期刊",c:"source"},
    {l:"状态",c:"summary_available",w:"50px"}, {l:"关键词",c:"tags_display"}, {l:"添加日期",c:"date_added",w:"100px"}
  ];
  const hr = tbl.createEl("thead").createEl("tr");
  for (const h of cols) {
    const th = hr.createEl("th", {
      text: h.l + (h.c && sortCol===h.c ? (sortAsc?" ▲":" ▼") : ""),
      attr:{style:"padding:6px 8px;border-bottom:2px solid var(--background-modifier-border);text-align:left;white-space:nowrap;"
        + (h.c?"cursor:pointer;":"") + (h.w?"width:"+h.w+";":"")}
    });
    if (h.c) th.addEventListener("click", () => { if (sortCol===h.c) sortAsc=!sortAsc; else {sortCol=h.c;sortAsc=true;} render(); });
  }

  const tbody = tbl.createEl("tbody");
  data.forEach((p, i) => {
    const tr = tbody.createEl("tr", {attr:{style:"border-bottom:1px solid var(--background-modifier-border);"+(i%2?" background:var(--background-secondary);":"")}});
    tr.createEl("td", {text:String(i+1), attr:{style:"padding:4px 8px;text-align:center;"}});

    // Title → PDF link
    const tdT = tr.createEl("td", {attr:{style:"padding:4px 8px;"}});
    const pp = String(p.pdf_path || "");
    if (pp) { const u="file:///"+pp.replace(/\\/g,"/").replace(/ /g,"%20"); tdT.createEl("a",{text:p.title||"Untitled",attr:{href:u,class:"external-link",style:"color:var(--text-accent);text-decoration:none;"}}); }
    else tdT.setText(p.title||"Untitled");

    // AI Summary → internal link
    const tdS = tr.createEl("td", {attr:{style:"padding:4px 8px;text-align:center;"}});
    if (p.summary_available) tdS.createEl("a",{text:"\uD83D\uDCDD",cls:"internal-link",attr:{"data-href":p.file.path,href:p.file.path,style:"text-decoration:none;font-size:1.2em;"}});
    else tdS.setText("—");

    const aus = p.authors||[];
    tr.createEl("td",{text:aus.length?String(aus[0]):"N/A",attr:{style:"padding:4px 8px;"}});
    tr.createEl("td",{text:String(p.year||""),attr:{style:"padding:4px 8px;text-align:center;"}});
    tr.createEl("td",{text:String(p.source||""),attr:{style:"padding:4px 8px;"}});
    tr.createEl("td",{text:p.summary_available?"✅":"❌",attr:{style:"padding:4px 8px;text-align:center;"}});
    const tags = (p.zotero_tags_original||[]).filter(t=>!String(t).startsWith("/"));
    tr.createEl("td",{text:tags.slice(0,3).join(", "),attr:{style:"padding:4px 8px;font-size:0.9em;"}});
    tr.createEl("td",{text:String(p.date_added||"").substring(0,10),attr:{style:"padding:4px 8px;white-space:nowrap;"}});
  });

  wrap.createEl("div",{attr:{style:"padding:4px 8px;font-size:12px;color:var(--text-muted);"}}).setText("显示 "+data.length+" / "+pages.length+" 篇");
}
searchInput.addEventListener("input", render);
tagSelect.addEventListener("change", render);
render();'''


def main() -> None:
    base = Path(__file__).resolve().parent
    state = json.loads((base / ".state" / "sync_state.json").read_text(encoding="utf-8"))
    items = state.get("items", {})
    raw_records = [r for r in items.values() if isinstance(r, dict)]

    seen: set[str] = set()
    records: list[dict] = []
    for r in raw_records:
        md = r.get("obsidian_md", "")
        if not md or not Path(md).exists():
            continue
        norm = str(Path(md).resolve())
        if norm in seen:
            continue
        seen.add(norm)
        records.append(r)

    records.sort(key=lambda r: r.get("date_added", ""), reverse=True)

    vault_dir = base / "obsidian_vault"
    dashboard = vault_dir / "00-Dashboard.md"

    all_tags: set[str] = set()
    for r in records:
        for tag in r.get("obsidian_tags", []):
            if tag != "zotero/unread":
                all_tags.add(tag)

    lines: list[str] = []
    lines.append("# 📄 Paper Dashboard")
    lines.append("")
    lines.append(f"> 自动生成于 {time.strftime('%Y-%m-%d %H:%M')} · 共 {len(records)} 篇论文")
    lines.append("> 需安装 **Dataview** 插件并开启 DataviewJS 查看交互式表格；下方同时提供静态 Markdown 表格。")
    lines.append("")

    # ── DataviewJS interactive section ──
    lines.append("## 交互式检索（DataviewJS）")
    lines.append("")
    lines.append("```dataviewjs")
    lines.append(DATAVIEWJS_BLOCK)
    lines.append("```")
    lines.append("")

    # ── Dataview: pending summaries ──
    lines.append("### 待总结论文")
    lines.append("")
    lines.append("```dataview")
    lines.append("TABLE WITHOUT ID")
    lines.append('  link(file.link, title) AS "标题",')
    lines.append('  authors[0] AS "第一作者",')
    lines.append('  year AS "年份",')
    lines.append('  dateformat(date(date_added), "yyyy-MM-dd") AS "添加日期"')
    lines.append('FROM "papers"')
    lines.append("WHERE codex_sync = true AND summary_available = false")
    lines.append("SORT date_added DESC")
    lines.append("```")
    lines.append("")

    # ── Static Markdown table ──
    lines.append("---")
    lines.append("")
    lines.append("## 静态总览表")
    lines.append("")
    lines.append("| # | 标题 (PDF) | AI总结 | 第一作者 | 年份 | 期刊 | 总结 | 关键词 | 添加日期 |")
    lines.append("|---|-----------|--------|----------|------|------|------|--------|----------|")

    for idx, r in enumerate(records, 1):
        title = r.get("title") or r.get("summary_stem") or "Untitled"

        # Title → PDF link
        pdf_path = r.get("pdf_path", "")
        if pdf_path and Path(pdf_path).exists():
            tl = f"[{esc(title)}]({pdf_file_uri(pdf_path)})"
        else:
            tl = esc(title)

        # AI Summary → Obsidian markdown link
        omd = r.get("obsidian_md", "")
        if omd and Path(omd).exists():
            rel = os.path.relpath(Path(omd), dashboard.parent).replace("\\", "/")
            summary_link = f"[📝]({rel})"
        else:
            summary_link = "—"

        authors = r.get("authors", [])
        fa = esc(str(authors[0])) if authors else "N/A"
        year = r.get("year") or ""
        source = esc(r.get("source") or "")
        has_sum = "✅" if r.get("summary_md") and Path(r.get("summary_md", "")).exists() else "❌"
        raw_tags = r.get("tags", [])
        dt = [t for t in raw_tags if not t.startswith("/")]
        tags_str = esc(", ".join(dt[:3])) if dt else ""
        da = r.get("date_added", "")[:10]
        lines.append(f"| {idx} | {tl} | {summary_link} | {fa} | {year} | {source} | {has_sum} | {tags_str} | {da} |")

    lines.append("")
    summarized = sum(1 for r in records if r.get("summary_md") and Path(r.get("summary_md", "")).exists())
    pending = len(records) - summarized
    lines.append("---")
    lines.append("")
    lines.append("## 统计")
    lines.append("")
    lines.append(f"- 总计：**{len(records)}** 篇")
    lines.append(f"- 已总结：**{summarized}** 篇")
    lines.append(f"- 待总结：**{pending}** 篇")
    lines.append(f"- 标签分类：**{len(all_tags)}** 个")
    lines.append("")

    dashboard.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Dashboard: {len(records)} papers, {summarized} summarized, {pending} pending")


if __name__ == "__main__":
    main()
