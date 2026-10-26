# 0.4.2 本地验证记录

日期：2026-10-23（Asia/Shanghai）。

最新任务 adeca6d363264549b74e6833c417213f 于本地 09:10:44 创建、09:11:31 失败，执行版本仍为 0.4.0。失败发生在出版信息校验：要求 journal，模型三次均准确返回正式期刊未知。此前安装 0.4.1 未更新旧服务进程。

对本次任务的 extracted.json 和 diagnostics.jsonl 中三份实际模型输出调用新 validate_metadata、complete_publication_date，再执行最终元数据校验，全部通过。识别 bioRxiv 预印本，journal 保留 null，使用原文 this version posted October 6, 2026 作为版本日期，不查询 Crossref、不调用模型。回放证明在 build/current-preprint-replay.json。

新增页面提交前版本核对以及后台已加载版本/磁盘版本检查，版本不一致时禁止创建新任务并提示在原终端重启。对仍不提供新配置字段的 0.4.0 服务，更新后的页面通过页面版本与服务版本比较也能给出提示。新后台返回 HTTP 409，包含 Ctrl+C 操作说明。已有任务不受影响，不进行动态模块重载。

验证：python -m unittest discover -s tests -q，共 89 项通过。node build/check_browser_version_guard.cjs 验证旧服务零 POST、一致版本可提交、磁盘更新后拦截。所有版本测试在隔离临时服务进行。

遵循用户在 VS Code 手动运行测试的要求，没有终止或重启 18765 上的原服务。源码和 paper2html 环境包更新后，原进程仍需用户在原终端重新启动并刷新页面。本次未重新调用 API 跑全文，没有宣称此任务已生成 Markdown/HTML。
