# 0.4.1 预印本支持验证

最新失败任务：`50d4940c12d647d98d2beff69948b4d8`，Enrich.AI，原始 PDF 4 页。

旧规则首先两次要求补齐 journal，随后要求正式发表日期及 Crossref 查询，最终因无正式日期失败。PDF 实际首页明确标注 `bioRxiv preprint` 和 `this version posted October 6, 2026.`，DOI 为 `10.64898/2026.09.29.755561`。DOI 中的日期不能替代当前版本发布日期。

## 修复及边界

- 首页明确平台标识决定是否为预印本，不信任模型自行添加的类型，也不因后页参考文献引用预印本而误分类。
- 期刊论文规则保持严格；已识别预印本不要求期刊名和正式期刊日期。bioRxiv/medRxiv 以当前版本 posted 日期为准，arXiv 保留原文版本号及日期。
- arXiv 没有 DOI 时用版本化 arxiv.org 来源链接，不虚构 DOI；有期刊 DOI 时也保留上传的 arXiv 版本链接。
- 预印本缺失版本日期时使用 undated，不从 DOI、编号、接受日期、版权年或生成时间猜测。
- 输出自动添加预印本说明，publication_source.kind=preprint_version，publication_date 是兼容字段，date_kind 明确其版本日期性质。预印本标识不代表已通过同行评审。

## 已完成验证

- `python -m unittest discover -s tests -q`：86 项通过。
- bioRxiv、无 DOI arXiv、有日期及日期未知 arXiv 的模拟模型完整流程生成 Markdown、HTML、ZIP，通过渲染与打包校验。
- 对最新失败任务保存的两次真实模型响应和原始 4 页提取文本回放，均通过元数据校验。结果为 bioRxiv、版本日期 20261006，journal 保持 null，无需联网查询日期。
- 版本日期冲突要求明确确认；标题或原文依据缺失仍拒绝；期刊论文不会因正文或后页引用预印本而改类型。

原失败任务记录保留，未声称该任务已完成全文阅读或生成新报告。用户在 VS Code 手动启动的旧进程未被终止；更新源码/安装包后需重启该进程并续跑，以加载新规则。
