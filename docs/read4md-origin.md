# 与 Read4MD 的关系

Paper2HTML 0.2.0 将 2026-10-21 本地 Read4MD 的图像、Markdown、HTML 和校验能力封装为独立应用。安装后不依赖 ReadPaper 或 Codex。

`paper2html/engine/` 内含适配相对导入后的 figure_format、extract_figures、render_markdown、render_html、validate_markdown、validate_html、bundle_article。应用另为图注发现补充 Fig./冒号形式，为文章支持 level=3 描述性小节。

应用自身的 SQLite 哈希／DOI／文献名称索引替代 `papers/article_list.csv`；原 PDF 保留于隔离任务目录。元数据、分块阅读及跨页图注由可替换的兼容 API 完成，证据引文按原文核验。

Read4MD 在交互环境中由执行智能体查看原图并决定区域。独立应用将此环节转为支持图片输入模型的有界定位与复核，或使用用户明确提供的区域计划。纯文本自动模式仅允许结构明确的单张位图；复杂图不猜测部分区域。

报告先在任务目录完整生成及校验，再复制至 reports。任务快照保持独立，重复生成归档公共报告旧版。输出一个 HTML，不生成额外微信版本。

API 调用与模型视觉判断不能替代人工科研审校；本项目不继承 Codex 的浏览、全文获取、OCR 或人工判断能力。
