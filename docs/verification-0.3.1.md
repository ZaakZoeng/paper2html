# 0.3.1 本地验证记录

验证日期：2026-10-22（Asia/Shanghai）。Windows / Python 3.10。

## 自动化回归

`python -m unittest discover -s tests -v`：37 项通过。

覆盖稳定 ID 局部修复、完整列表兼容、连续片段编号数组、双栏阅读顺序、缺省图片页码、检查点续跑、接口错误分类和带图报告打包。

## 真实论文与 API 验证

输入：用户指定的 *Autonomous biomedical research with an artificial intelligence agent* PDF，共 12 页。使用用户在本地网页配置的 API，模型 ID 为 `gpt-6.1-sol`。没有将密钥写入验证记录。

成功任务：`36ed2bf25fcc4b46a87f74c335dca065`。复用已校验的出版信息与 4 个阅读分块，完成 5 张主图图注核对、原图定位与视觉核对、文章生成及打包。最终任务状态为 `completed`。

输出目录：`reports/20260709_Science_Biomni/`，包含 Markdown、单文件 HTML、5 张原图及 JSON 校验记录。

从 HTTP 下载 ZIP 后独立复查：

- Markdown：6527 字符，5 张图，5 条中文完整图注。
- HTML：5 张内嵌图片；正文、链接及标题结构与 Markdown 一致。
- 74 条证据引文的 PDF 页码和文本匹配通过。
- 包内 14 个文件的 SHA-256 与清单一致。
- 原 PDF SHA-256 与用户指定的本地 PDF 一致。
- 对照 PDF 检查了 5 张裁切图，并在浏览器检查了 HTML 排版。

这些检查确认本次样例能生成两种输出及图片资产；不代表所有模型、API 或其他论文均已得到验证，也不等于逐条科学事实和翻译已完成人工审校。
