# Obsidian Inbox 导出说明

## 导出结构

默认结果：

```text
<Vault>/<Inbox>/
├── <标题>.md
├── <标题>.assets/
│   ├── <frame_id>_<HH-MM-SS>.jpg
│   └── ...
└── <标题>.data/
    ├── document.json
    ├── timeline.json
    ├── sources.json
    └── 可选转写或字幕
```

Markdown 中的图片使用相对于笔记的 POSIX 风格路径，不引用任务工作区或开发机临时目录。图片名保留 frame_id 和媒体时间；Document/source sidecar 保留 ASR segment、OCR event/block、frame、原始响应和时间范围。

## 路径规则

- Vault 必须是已存在的绝对目录，应用不会猜测或创建用户正式 Vault。
- Inbox 必须是 Vault 内相对路径，不允许 `..` 越界。
- Inbox 和其内部结果目录可按需创建。
- 文件名会移除 Windows 禁止字符、保留名和不安全尾部字符。
- 本地源媒体不会复制或修改；URL 媒体仍保留在任务工作区。

## 冲突策略

- `rename`：默认策略，自动生成不冲突的 stem，保留已有笔记。
- `fail`：任何同名 Markdown、附件或数据目录存在时停止并提示。
- `overwrite`：先把所有既有目标移动到同一 Inbox 内的临时备份；新 bundle 全部提交成功后才删除备份，失败时恢复旧结果。

文件名、附件目录和数据目录模板在创建任务时冻结到快照 v5。重试不会因后来修改全局设置而改变原任务的导出语义。

## 原子性、取消与回滚

Exporter 先在 Inbox 内的 `.video2doc-export-*.tmp` 目录构建完整 bundle。图片、sidecar 和 Markdown 全部成功并再次检查取消状态后，才依次移动到最终目标。

异常或取消时会：

1. 删除已经提交的新目标；
2. 恢复 overwrite 模式的旧目标；
3. 删除自身 stage/backup 临时目录；
4. 保留任务工作区内可重试的上游产物。

因此不会把半个 Markdown 当成成功结果，也不会在 Vault 中长期留下 Video2Doc 临时目录。

## Markdown 内容

Renderer 输出：

- 可解析 YAML frontmatter；
- 标题、来源、创建时间、时长、语言；
- ASR/OCR/文本模型和处理配置；
- 按媒体顺序组织的描述性分节；
- `[HH:MM:SS]` 时间戳；
- 语音、画面文字和关键画面；
- source_id 与可折叠追溯附录。

仅对支持标准时间跳转的网页 URL 生成链接；本地文件保留显示时间戳和结构化源引用，不伪造不可用链接。文本模型不是 Markdown 输出的依赖。

## 测试目录

仓库真实验收使用：

```text
D:\Video2Doc-Test\Obsidian-Test-Vault
```

这是独立测试 Vault，不代表或覆盖用户正式 Vault。正式路径应在应用设置中配置。
