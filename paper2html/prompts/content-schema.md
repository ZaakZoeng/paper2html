# Illustrated article content schema

Use UTF-8 JSON. Metadata and evidence retain PDF provenance; the article's visible figure labels do not show PDF page numbers or editorial placeholders.

```json
{
  "metadata": {
    "source_pdf": "papers/20260101_Journal_Example.pdf",
    "title": "Formal paper title",
    "authors": "First Author et al.",
    "journal": "Example Journal",
    "journal_abbr": "Journal",
    "publication_date": "20260101",
    "doi": "10.1234/example",
    "short_name": "Example",
    "one_line_summary": "一句话说明研究问题、方法、结果与边界"
  },
  "article": {
    "headline": "中文标题，不含期刊缩写和分隔符",
    "deck": "可选副标题",
    "lead": ["导语"],
    "sections": [
      {"heading": "论文一句话", "paragraphs": ["研究问题、结果与边界。"]},
      {"heading": "研究背景", "paragraphs": ["背景。"], "figures": [
        {"source": "Figure 1A-B", "pdf_page": 4, "title": "完整中文图题",
         "caption_zh": ["（A）A分面的忠实完整翻译", "（B）B分面的忠实完整翻译"]}
      ]}
    ],
    "conclusion": ["贡献与局限。"],
    "reference": "含 DOI URL 的规范原文信息"
  },
  "evidence": [{"claim": "重要结论", "pages": [4, 5], "note": "结果与限定条件"}]
}
```

Require all metadata fields used above except `source_pdf`. Date must be a real calendar date in `YYYYMMDD`. For supported historical content, `model_name` can supply a missing `short_name`. The Markdown renderer adds `# 期刊缩写｜标题内容` and the fixed opening citation, so do not add a journal prefix to `article.headline`.

Require exactly one `论文一句话`, at least one `研究背景`, and dedicated `conclusion` / `reference` fields rather than repeated conclusion/reference sections. Ground quantitative, comparative and limitation claims in the evidence list; unknown values must not be invented.

Each numbered main figure appears once. `source` retains the source label (e.g. `Figure 3A-G`), `pdf_page` is a one-based PDF page and `caption_zh` is a nonempty array covering all panels, lead-in text, sample sizes, metrics, conditions and abbreviations. Optional `clip`, `regions` or `xref` fields can supply a verified extraction plan; see `figure-extraction.md`. Readable main captions are mandatory; supplementary content is optional unless material.

Use `bundle_article.py` with the source PDF. It produces `reports/YYYYMMDD_Journal_ShortName/`, extracts assets and renders:

```markdown
![Figure 3](assets/figure-3.png)

Figure 3，UniCure解析机制、剂量、细胞状态与联合用药依赖的响应。  
（A）完整的A分面图注；（B）完整的B分面图注。
```

The two trailing spaces after the figure title produce a Markdown line break. The public title uses `Figure N` only; panel labels remain inside the complete caption. Never display `配图位置`, `中文图注` or `PDF 第…页` as caption prefixes. Original labels, PDF pages and extraction details belong in `figures.json`, `provenance.json` and the structured content, not in visible editorial notes.
