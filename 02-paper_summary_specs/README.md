# Paper Summary Specs

这个文件夹专门放总结规范文件。

## 你以后最短的指令

### 默认规范

```text
按默认规范总结并打包 01-paper/xxx.pdf
```

### 指定规范

```text
按规范 compact 总结并打包 01-paper/xxx.pdf
```

我会把 `compact` 自动解析为：

```text
02-paper_summary_specs/compact.md
```

你不需要再输入：

- 规范文件完整路径
- 输出 Markdown 文件名
- 打包目录名称

这些都会自动按默认规则生成。

## 当前文件

- `default.md`：默认规范
- `template.md`：自定义规范模板

## 推荐做法

1. 默认情况直接用：

```text
按默认规范总结并打包 01-paper/2507.10356v2.pdf
```

执行时会先重新读取磁盘上的最新 [default.md](/csot_nas/homesCsOT/CsOT_Record/读文章/wzy/02-paper_summary_specs/default.md)，不会沿用旧会话里记住的规范内容。

2. 如果你想自定义一套规则：

- 复制 `template.md`
- 改名成短名字，例如 `compact.md`
- 修改内容

然后直接说：

```text
按规范 compact 总结并打包 01-paper/2507.10356v2.pdf
```

无论默认规范还是自定义规范，行内公式都应使用 `$...$`，不要用反引号包裹数学内容。
