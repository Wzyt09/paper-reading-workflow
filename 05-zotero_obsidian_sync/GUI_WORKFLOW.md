# Paper Sync Manager 使用说明

## 1. 入口

直接双击：

- `05-zotero_obsidian_sync\paper_sync_gui.cmd`

这会打开一个图形界面，用来完成以下几件事：

- 监视 `01-paper` 中新增的 PDF
- 将选中的 PDF 自动加入 Zotero 集合 `01-paper-sync`
- 对 Zotero 条目生成或复用 AI 总结
- 将 `01-paper-sync` 中的全部条目统一导出到 Obsidian 文件夹

## 2. 你现在得到的工作流

当前流程已经做了两件关键自动化：

- 导入 PDF 时，程序会自动把 Zotero 切到 `01-paper-sync`
  你不再需要先手动在 Zotero 里点中这个集合。
- 同步到 Obsidian 时，会统一导出 `01-paper-sync` 的全部条目
  已总结条目导出完整总结包；未总结条目也会生成基础元数据页，不会再缺席。

## 3. 界面说明

### 3.1 `01-paper Monitor`

这个标签页面向“刚下载到 `01-paper` 的新文献”。

会显示两个列表：

- `本次新增`
  只显示这个窗口打开之后，新出现在 `01-paper` 中的 PDF。
- `尚未同步`
  显示当前在 `01-paper` 中、但还没有匹配到 Zotero 集合 `01-paper-sync` 的 PDF。

常用按钮：

- `Add To Zotero / 添加至 Zotero`
  将选中的 PDF 送入 Zotero。
- `Open Selected PDF`
  打开本地原始 PDF。

导入逻辑：

- 需要 Zotero 桌面端处于打开状态。
- 程序会先自动选中 `01-paper-sync` 集合，再调用 Zotero Connector 的本地接口导入附件。
- 文献元数据识别仍然由 Zotero 自己完成，所以导入后请给 Zotero 一点时间去抓取标题、作者、年份等信息。

### 3.2 `01-paper-sync Collection`

这个标签页面向“已经进入 Zotero 的条目”。

它会读取本地 Zotero 数据库，并按加入时间显示 `01-paper-sync` 中的顶层条目。常见列包括：

- `Added`
- `Title`
- `Year`
- `PDF Source`
- `Summary`
- `Tags`

常用按钮：

- `Generate / Attach Summary`
  对选中的 Zotero 条目执行总结流程。
- `Open Selected PDF`
  打开条目对应的 PDF。
- `Open Output`
  打开该条目对应的总结输出目录或总结 Markdown。

总结逻辑：

- 如果目标总结 Markdown 已经存在，程序会直接复用，不会重复总结。
- 如果不存在，就按 `02-paper_summary_specs/default.md` 的最新内容重新生成。
- 生成过程中，窗口底部日志区会实时显示 `scan`、`extract`、`summarize`、`package`、`obsidian`、`zotero` 等步骤输出，便于查看进度。

### 3.3 顶部工具栏

常用入口：

- `Refresh`
  重新扫描 `01-paper` 和 `01-paper-sync`。
- `Open 01-paper`
  打开原始 PDF 文件夹。
- `Open default.md`
  打开默认总结规范文件，方便你在生成前修改要求。
- `Open Guide`
  打开本说明文档。
- `Open Obsidian Vault`
  打开 Obsidian 导出根目录。
- `Sync Collection To Obsidian`
  将整个 `01-paper-sync` 重新导出到 Obsidian。
- `Stop Task`
  中止当前正在运行的总结任务。

## 4. Sync Collection To Obsidian 现在会做什么

点击 `Sync Collection To Obsidian`，等价于运行：

- `05-zotero_obsidian_sync\sync_collection_to_obsidian.cmd`

它会做以下几件事：

- 扫描当前工作区中已经打包过的总结目录
- 将这些已总结条目同步到 `05-zotero_obsidian_sync\obsidian_vault`
- 对 `01-paper-sync` 中尚未总结的条目，生成一个基础元数据页面
- 给每个条目建立统一目录，目录下至少包含：
  - 该条目的 Markdown 页面
  - 一个按文献标题重命名后的 PDF 副本
  - 如果已有总结，还会包含总结文档及相关材料
- 依据 Zotero 标签重建 Obsidian 标签索引
- 如果条目已经有本地总结 Markdown，则将它作为链接附件回挂到 Zotero

当前统一导出目录为：

- `05-zotero_obsidian_sync\obsidian_vault\papers`

也就是说，Obsidian 里现在不只看得到“已经总结过的条目”，而是能看到 `01-paper-sync` 的全部条目。

## 5. Zotero 中总结附件的写回方式

现在总结 Markdown 回写到 Zotero 时使用的是：

- `linked_file`

这意味着：

- Zotero 条目下会挂一个“链接附件”
- 指向的是你本地工作区中的 `.md` 文件
- 不会占用 Zotero Storage 配额

之所以这样处理，是因为你当前 Zotero 云附件空间已经满了，而本地工作区和 Obsidian 恰好更适合保存这些 AI 总结文件。

相关配置在：

- `05-zotero_obsidian_sync\config.json`

关键字段是：

- `zotero_api.summary_attachment_mode = "linked_file"`

## 6. 推荐的日常使用顺序

1. 浏览文章时，把需要保存的 PDF 下载到 `01-paper`。
2. 打开 Zotero。
3. 双击 `05-zotero_obsidian_sync\paper_sync_gui.cmd`。
4. 在 `01-paper Monitor` 中选中文献，点击 `Add To Zotero / 添加至 Zotero`。
5. 等 Zotero 自动识别出元数据。
6. 切到 `01-paper-sync Collection`，选中对应条目，点击 `Generate / Attach Summary`。
7. 在底部日志区查看实时进度，等待总结完成。
8. 如需刷新 Obsidian 统一资料库，点击 `Sync Collection To Obsidian`。
9. 在 Obsidian 中按标签、作者、年份、标题继续整理和检索。

## 7. 当前边界

- Zotero 桌面端必须开着。
- 自动导入和自动挂接依赖本机的 Zotero 本地接口与本地数据库。
- AI 总结本身仍然依赖你当前可用的总结方式。
  目前如果没有可调用的 OpenAI API / Codex CLI，程序不能自动生成新总结，但仍可复用已有总结并完成 Zotero、Obsidian 的同步。

## 8. 常用文件

你平时最常用的几个入口如下：

- `02-paper_summary_specs\default.md`
- `05-zotero_obsidian_sync\paper_sync_gui.cmd`
- `05-zotero_obsidian_sync\sync_collection_to_obsidian.cmd`
- `05-zotero_obsidian_sync\obsidian_vault`
- `05-zotero_obsidian_sync\GUI_WORKFLOW.md`
