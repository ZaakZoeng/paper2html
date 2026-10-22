# Read4MD Agent · Paper2HTML

可在本地部署的中文论文阅读智能体。用户上传 PDF、填写自己的模型 API，智能体自动按页阅读、整理原文证据、翻译完整主图图注、提取论文原图，并生成 **带图片的 Markdown 和一个可独立打开的 HTML**。

不需要安装 Codex，也不依赖原 ReadPaper 目录或 `.codex/skills`。`paper2html/engine/` 内已打包 Read4MD 的图像提取、排版和校验模块。

## 0.3.2 更新

- 期刊文件标签和论文短名由程序自动生成，不再因为 PDF 未提供官方缩写或模型返回 null 而失败；这些标签不是新推断的出版事实。
- 从全文查找明确的正式发表日期，优先保留晚页日期所在的上下文。收稿、接受、版权和 PDF 创建日期均不作为发表日期。
- PDF 缺少正式日期时，默认仅将 DOI 发给 Crossref，核对 DOI、论文标题和期刊后使用完整的正式发表日期。日期优先在线发表，其次印刷发表；不使用登记时间或不完整的年月信息。来源保存到任务及校验报告中，网页可打开来源链接。
- 可在高级选项关闭 DOI 查询，CLI 对应 `--offline-metadata`，HTTP 配置为 `metadata_lookup:false`。网络不可用或来源不匹配时，直接提示补充 `publication_date`，不让模型反复猜测。
- 保持 0.3.1 失败任务的续跑兼容性。密钥仍只在内存中使用，不发送给 Crossref。

50 项回归测试通过，最新失败任务的三次真实返回均已回放通过，真实续跑已越过出版信息阶段；见 [0.3.2 验证记录](docs/verification-0.3.2.md)。

## 0.3.1 更新

修复真实双栏论文验证中发现的问题：PDF 提取保留原生阅读顺序；图注支持同页连续的片段编号数组；局部修复按稳定 ID 对应，同时兼容完整清单响应。每次校验失败会保存脱敏的 `diagnostics.jsonl`，可从 `GET /api/jobs/<id>/files/diagnostics` 下载。0.3.0 失败任务可在相同配置下续跑，但旧文本提取方式的检查点不会复用，以避免将混排的原文继续用于文章生成。

已通过 37 项回归测试，并使用用户配置的真实 API 跑通 Biomni 论文，生成含 5 张原图的 Markdown 与单文件 HTML；见 [本地验证记录](docs/verification-0.3.1.md)。

## 0.3.0 更新

- 修复 Windows 默认 GBK 编码下的中文 JSON、提示词和报告读写错误，所有文本文件明确使用 UTF-8。
- 阅读阶段提供原文片段编号，由程序回填引文与 PDF 页码。仍接受逐字引文；只规范化空白、常见连字和换行断词，不使用模糊匹配放过虚构证据。
- 校验失败显示具体条目、页码和引文；两次修复只重试失败条目，保留已经通过的证据和图注。
- 成功的出版信息、阅读分块、图注合并和文章生成步骤保存检查点。新版本失败任务可重新提供密钥续跑，复用前会再次校验。PDF 提取、原图定位与视觉核对仍会重新执行。
- 新增独立对比运行，同一论文可以使用不同 API 分别处理，独立报告保存在带任务 ID 的目录中，不替换已有结果。
- 区分空接口响应、接口非 JSON、模型正文非 JSON、权限拒绝和输出截断；新增连接与 JSON 输出测试、最近任务列表、实时用量与等待状态。

更新本地安装后，需要停止旧服务并重新启动才能加载新版 Python 代码。不要在任务仍运行时强行重启。旧任务与报告保留，但 0.2.0 任务没有配置指纹和检查点，需要重新提交。

```bash
python -m pip install --no-deps --no-build-isolation .
python -m paper2html --version
python -m paper2html serve --port 18765 --data-dir data --reports-dir reports
```

网页高级选项中，分别选择“重新生成”或“独立对比运行”。续跑时填写失败任务 ID，重新选择相同 PDF，并提供相同接口、模型、最大输出、视觉/JSON 选项、补充信息和配图计划；密钥可以更换。续跑会创建新的任务并保留失败记录，token 用量只计算新任务实际发出的调用。默认不自动复用其他任务的检查点。

CLI 对应选项为 `--independent` 和 `--resume-from <任务ID>`。HTTP `POST /api/jobs` 增加可选顶层字段 `independent`（布尔值）、`resume_from`（失败任务 ID）；`GET /api/jobs` 返回最近 50 个任务；`POST /api/test` 接受 `{"config":{...}}`，使用与任务提交相同的令牌和来源校验，仅测试文本 JSON 输出，会产生一次正常 API 调用（遇到临时网络故障或限流最多尝试三次）。

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

已在 Windows / Python 3.10 本地环境验证测试及模拟模型完整流程；未验证 macOS，也未用真实付费 API 验证本次改动。

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

- **Windows 启动报 WinError 10013 / 无法监听端口**：表示系统拒绝绑定指定的本地端口，可能是保留端口、独占占用或安全策略。先运行 `python -m paper2html serve --port 18765`，打开 `http://127.0.0.1:18765`；也可用 `--port 0` 让系统分配端口，并打开终端实际打印的 URL。排查时运行 `netsh interface ipv4 show excludedportrange protocol=tcp` 查看保留范围，以及 `netstat -ano | findstr :8765` 查看端口占用。没有占用记录也不能排除系统保留或安全策略限制。
- **模型不支持图片**：关闭视觉选项；复杂原图提供区域计划，或更换支持图片输入的模型。视觉定位/核对失败会终止报告生成。
- **日期或 DOI 无法核实**：填写补充信息，不从收稿日期或文件名猜测。当前不支持无 DOI 论文。
- **模型输出截断**：提高 `max_tokens`（1024–32768），或换输出容量更大的模型。长图注也计入输出。
- **连接失败或 HTTP 401/429**：检查 Base URL、模型 ID、密钥、额度和网络。没有对 API Key 或服务商额度作假设。
- **PDF 没有文字**：先 OCR。提取不到的结果、表格或图中文字不能由模型猜测补全。
- **同一篇重复提交**：默认打开已有任务。需重新分析时选择“重新生成”；受管理的旧报告会归档，无关同名目录不会覆盖。
- **重启后任务中断**：密钥不落盘，运行中任务会标为中断；0.3.0 新任务可重新提供相同配置，填写失败任务 ID 续跑。旧任务需重新提交。

程序检查引文匹配、格式、图号覆盖、图片资产和文件哈希；视觉模式增加模型对原图区的判断。这些都不能证明全部事实、翻译、图像完整性和研究结论已经人工核准。生成后仍需对照 PDF 复核。测试使用模拟模型及本地兼容 HTTP 服务；真实模型和网关兼容性需在实际配置中验证。

开发验证：

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m pip wheel --no-deps . --wheel-dir dist
```

源码分层：`agent.py` 编排阅读与证据，`provider.py` 连接兼容 API，`figures.py` 定位和核对原图，`engine/` 提取/排版/校验，`store.py` 管理任务，`server.py` 与 `web/` 提供本地网页，`cli.py` 提供命令行入口。
