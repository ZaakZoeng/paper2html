# Read4MD Agent · Paper2HTML

可在本地部署的中文论文阅读智能体。用户上传 PDF、填写自己的模型 API，智能体自动按页阅读、整理原文证据、翻译完整主图图注、提取论文原图，并生成 **带图片的 Markdown 和一个可独立打开的 HTML**。

不需要安装 Codex，也不依赖原 ReadPaper 目录或 `.codex/skills`。`paper2html/engine/` 内已打包 Read4MD 的图像提取、排版和校验模块。

## 当前功能

- 输入本地可搜索 PDF，最多 25 MB、150 页；扫描件需先 OCR。
- 配置自己的 API Base URL、模型名称和 API Key，支持 Chat Completions 兼容服务，包括提供该接口的本地模型服务。
- 分块覆盖全文、检查证据引文确实出现在指定 PDF 页、合并跨页图注，再生成中文文章。
- 支持图片输入的模型可定位矢量图、拼图和跨页图，并对提取结果再做一次视觉核对。
- 纯文本模型可自动提取结构明确的完整单张位图；复杂图需要用户提供已核对的区域计划。遇到无法确认的图会失败并保留预览，避免静默丢图后宣称完成。
- 每篇报告一个目录：原图 `assets/`、Markdown、**一个 HTML**、结构化内容、证据、来源及校验记录。网页支持 ZIP 整包下载。
- PDF 哈希、DOI 和文献名称查重。明确重新生成时，旧报告归档；每个历史任务保留其独立结果。
- 网页与命令行两种使用方式；Linux、macOS、Windows 使用各自平台的目录进程锁。

原生 Anthropic Messages、Gemini 等不同协议需经兼容网关转换，本项目不自动将任意厂商协议转换成 Chat Completions。目前入口为 PDF，不含 DOI 自动下载、付费全文获取或 OCR。

## 1. 安装

需要 Python 3.10 或更高版本。在本项目目录运行。

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
```

Windows 无需执行激活脚本，可直接使用下面的 Python 路径启动。

本次验证环境为 Linux / Python 3.10；已适配 Windows 锁和启动方式，但尚未在 Windows/macOS 实机测试。

## 2. 启动网页

```bash
python -m paper2html serve --port 8765 --data-dir data --reports-dir reports
```

浏览器打开 **http://127.0.0.1:8765**。安装后的 `paper2html serve` 与上面的命令等价；旧写法 `python -m paper2html --port 8765` 也保留。

Windows：

```powershell
.\.venv\Scripts\python.exe -m paper2html serve --port 8765 --data-dir data --reports-dir reports
```

网页操作：

1. 选择 PDF。
2. 填写 Base URL、模型 ID、API Key。Base URL 通常填到 `/v1`，也可填写完整 `/chat/completions` 地址；请遵循自己的服务商说明。
3. 默认开启“视觉核对与复杂原图定位”，此时模型必须支持 `image_url` 图片输入。纯文本模型请在高级选项关闭它。
4. PDF 未写明正式发布日期等必要信息时，填写“补充出版信息”；不知道的信息请查明后填写。
5. 点击“开始阅读”，等待任务完成。可查看调用次数、服务商返回的 token 统计、警告和阶段进度。
6. 打开 HTML 阅读；下载 ZIP 保留 Markdown 所需的图片。单独下载 Markdown 时，必须同时保留对应 `assets/`。

HTML 已内嵌原图，可以单文件移动和离线打开。复制到微信公众号编辑器时，样式和内嵌图片可能被编辑器过滤，需要上传 `assets/` 原图并检查移动端预览。本项目不会自动发布文章。

### API 和密钥配置

支持在网页为每个任务单独选择服务，也支持通过环境变量设置默认值。

```bash
export READ4MD_BASE_URL='https://your-provider.example/v1'
export READ4MD_MODEL='your-model-id'
export READ4MD_API_KEY='your-key'
python -m paper2html serve
```

PowerShell：

```powershell
$env:READ4MD_BASE_URL = 'https://your-provider.example/v1'
$env:READ4MD_MODEL = 'your-model-id'
$env:READ4MD_API_KEY = 'your-key'
.\.venv\Scripts\python.exe -m paper2html serve
```

密钥仅供进程内调用，不写入任务文件、SQLite、前端存储或默认配置接口。网页中的非空输入覆盖环境变量；密钥输入留空则使用服务端环境变量。`.env` 不会被自动读取。不要把密钥提交到代码仓库。

本地兼容服务示例：Base URL `http://127.0.0.1:8000/v1`，模型名称使用该服务实际加载的 ID。无鉴权服务可留空密钥。模型必须能输出较长 JSON；是否支持视觉输入和 JSON mode 取决于所接入服务。

远程 API 地址使用 HTTPS；HTTP 仅允许 localhost / 127.0.0.1 / ::1。论文文本会发送到指定服务，视觉模式还会发送相关 PDF 页和提取图片。

### 补充出版信息

网页输入 JSON，命令行可保存为 `metadata.json`：

```json
{
  "publication_date": "20260909",
  "journal": "Nature",
  "journal_abbr": "Nature",
  "short_name": "ProteinTalks"
}
```

可用字段：`title`、`authors`、`journal`、`journal_abbr`、`publication_date`、`doi`、`short_name`。日期为正式在线发表日期；预印本使用对应版本发布日期。`journal_abbr` 和 `short_name` 应含可用于文件名的 ASCII 字符。程序会记录用户补充字段，不能将其视为 PDF 中已核实的内容。

## 3. 命令行生成

先设置上面的三个环境变量，再运行：

```bash
python -m paper2html run /path/to/paper.pdf --reports-dir reports
```

指定纯文本模型、补充信息和原图区域：

```bash
python -m paper2html run /path/to/paper.pdf \
  --base-url http://127.0.0.1:8000/v1 --model local-model \
  --text-only --metadata metadata.json --figure-plan figure-plan.json
```

其他选项：`--data-dir`、`--max-tokens`（默认 12000）、`--timeout`（默认 180 秒）、`--json-mode`、`--regenerate`。密钥通过环境变量提供，避免出现在命令行参数中。`run` 成功或找到重复任务时退出码为 0，失败为 1；输出任务 JSON 包含报告目录和错误原因。启动网页与运行 CLI 不应同时使用同一个 `data-dir`。

### 复杂图的区域计划

视觉模型会自动选择并核对完整区域。纯文本模型遇到矢量或复合图时，任务将提示查看：

```text
data/<任务ID>/figure-review/assets/review/
```

根据 PDF 核对完整图区，填写以下 JSON 并重新提交；失败任务会释放查重占位，无需勾选重新生成。坐标为 PDF 点，原点在页面左上角，页码从 1 开始；包含全部分面、图例、尺度，排除正文和图注。

```json
{
  "figures": [
    {"source":"Figure 1", "pdf_page":3, "clip":[50,80,550,500]},
    {"source":"Figure 2", "pdf_page":5, "regions":[
      {"pdf_page":5,"clip":[50,80,550,600]},
      {"pdf_page":6,"clip":[50,80,550,300]}
    ]}
  ]
}
```

坐标仅演示格式，不能直接用于其他论文。计划必须覆盖该论文全部正文主图。已确认某个 PDF 内嵌图像包含完整主图时也可提供 `xref`；可选 `source_pdf_sha256` 用于绑定源文件。程序会校验页码、坐标范围及图号，不会证明用户所选区域的科学完整性。

## 4. 输入与输出

默认工作目录结构：

```text
data/
  jobs.sqlite3
  <任务ID>/
    source.pdf
    extracted.json
    metadata.json
    notes-001.json ...
    figure-plan.json
    visual-review.json
    report.zip
    reports/<文献名>/ ...     # 任务自己的报告快照
reports/
  YYYYMMDD_Journal_ShortName/
    assets/figure-1.png ...
    YYYYMMDD_Journal_ShortName.md
    YYYYMMDD_Journal_ShortName.html
    content.json
    evidence.json
    figures.json
    provenance.json
    validation.json
    visual-review.json
    figure-plan.json
    bundle.json
  .history/ ...               # 明确重新生成时的旧版本
```

下载 ZIP 内保留 `reports/<文献名>/` 结构。日期来自论文发布日期，和生成日期无关。原始 PDF 保留在任务目录，不改动用户上传前的文件。缓存、快照及归档会占用磁盘空间。

一级章节“论文一句话、研究背景、模型架构（如适用）、研究结果、贡献与局限、结语”保留浅蓝底、蓝色边框和居中粗体样式。其他标题及“原文信息”为 **16px、粗体、`#ffa900`**。正文 16px，完整中文图注 14px。

图注公开格式为原图、`Figure N，中文图题。` 和完整分面图注；PDF 页码、原始图号、裁切坐标和哈希放入 JSON，不显示“配图位置”或“中文图注”等占位标签。

## 5. HTTP API 接入

先启动服务。读取 `GET /api/config` 获得 `csrf_token`，再提交 `POST /api/jobs`，请求头包含 `Content-Type: application/json`、`X-Paper2HTML-Token: <令牌>`。

```json
{
  "filename":"paper.pdf",
  "pdf_base64":"<PDF字节的base64>",
  "config":{
    "base_url":"https://your-provider.example/v1",
    "model":"your-model-id",
    "api_key":"your-key",
    "max_tokens":12000,
    "timeout":180,
    "json_mode":false,
    "vision":true
  },
  "metadata":{},
  "regenerate":false
}
```

`figure_plan` 是可选顶层 JSON 对象。轮询 `GET /api/jobs/<id>`，状态为 `queued`、`running`、`completed`、`duplicate`、`failed`。重复任务包含 `existing_job`；完成任务包含 `artifacts`。

结果地址：`GET /api/jobs/<id>/files/<类型>`，类型可为 `markdown`、`html`、`bundle`（ZIP）、`evidence`、`content`、`figures`、`provenance`、`validation`。HTML 默认浏览，附加 `?download=1` 下载。下载来自该任务的独立快照，不会因后续重生成而变成其他版本。

服务绑定 127.0.0.1，面向单用户本地使用，未提供多用户账户与互联网部署认证。服务器远程运行时可通过 SSH 隧道访问：

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

## 6. 常见问题与验证边界

- **模型不支持图片**：关闭视觉选项；复杂原图提供区域计划，或更换支持图片输入的模型。视觉定位/核对失败会终止报告生成。
- **日期或 DOI 无法核实**：填写补充信息，不从收稿日期或文件名猜测。当前不支持无 DOI 论文。
- **模型输出截断**：提高 `max_tokens`（1024–32768），或换输出容量更大的模型。长图注也计入输出。
- **连接失败或 HTTP 401/429**：检查 Base URL、模型 ID、密钥、额度和网络。没有对 API Key 或服务商额度作假设。
- **PDF 没有文字**：先 OCR。提取不到的结果、表格或图中文字不能由模型猜测补全。
- **同一篇重复提交**：默认打开已有任务。需重新分析时选择“重新生成”；受管理的旧报告会归档，无关同名目录不会覆盖。
- **重启后任务中断**：密钥不落盘，运行中任务会标为中断；重新提交即可。

程序检查引文匹配、格式、图号覆盖、图片资产和文件哈希；视觉模式增加模型对原图区的判断。这些都不能证明全部事实、翻译、图像完整性和研究结论已经人工核准。生成后仍需对照 PDF 复核。当前没有真实付费 API 的验证凭据，测试使用模拟模型及本地兼容 HTTP 服务；Windows/macOS 未实机验证。

开发验证：

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m pip wheel --no-deps . --wheel-dir dist
```

源码分层：`agent.py` 编排阅读与证据，`provider.py` 连接兼容 API，`figures.py` 定位和核对原图，`engine/` 提取/排版/校验，`store.py` 管理任务，`server.py` 与 `web/` 提供本地网页，`cli.py` 提供命令行入口。
