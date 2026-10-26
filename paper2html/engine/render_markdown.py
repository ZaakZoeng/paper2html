#!/usr/bin/env python3
import argparse
from datetime import datetime
import json
import re
from pathlib import Path
from .figure_format import figure_id, public_caption, write_file
from ..publication import is_preprint, reference_url, date_component, preprint_notice


def safe_component(value):
    value = re.sub(r"\s+", "-", str(value).strip())
    value = re.sub(r"[^A-Za-z0-9._-]", "", value)
    return value.strip("._-")


def doi_url(value):
    doi = str(value).strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi, flags=re.I):
        raise ValueError("doi must be a DOI or doi.org URL")
    return "https://doi.org/{}".format(doi)


def validate_content(data):
    for key in ("metadata", "article", "evidence"):
        if key not in data:
            raise ValueError("Missing content key: {}".format(key))
    metadata = data["metadata"]
    for key in (("title", "journal_abbr", "authors", "preprint_server", "one_line_summary") if is_preprint(metadata) else ("title", "journal_abbr", "publication_date", "authors", "journal", "doi", "one_line_summary")):
        if not metadata.get(key):
            raise ValueError("Missing metadata key: {}".format(key))
    date_component(metadata)
    short_name = metadata.get("short_name") or metadata.get("model_name")
    if not short_name:
        raise ValueError("Missing metadata key: short_name")
    article = data["article"]
    for key in ("headline", "sections", "conclusion", "reference"):
        if not article.get(key):
            raise ValueError("Missing article key: {}".format(key))
    if any(
        separator in article["headline"]
        for separator in ("|", "｜")
    ):
        raise ValueError("article.headline must not include the journal prefix or separator")
    reference_url(metadata)
    headings = [section.get("heading") for section in article["sections"]]
    if headings.count("论文一句话") != 1:
        raise ValueError("Article must contain exactly one 论文一句话 section")
    if "研究背景" not in headings:
        raise ValueError("Article must contain 研究背景")
    if any(heading in ("结语", "原文信息") for heading in headings):
        raise ValueError("结语 and 原文信息 must use their dedicated article fields")
    for section_index, section in enumerate(article["sections"], 1):
        for figure_index, figure in enumerate(section.get("figures", []), 1):
            for key in ("source", "pdf_page", "title", "caption_zh"):
                if not figure.get(key):
                    raise ValueError(
                        "Figure {}.{} requires {}".format(section_index, figure_index, key)
                    )
            if not isinstance(figure["pdf_page"], int) or figure["pdf_page"] < 1:
                raise ValueError("Figure {}.{} has an invalid pdf_page".format(section_index, figure_index))
            if not isinstance(figure["caption_zh"], list) or any(
                not isinstance(item, str) or not item.strip()
                for item in figure["caption_zh"]
            ):
                raise ValueError("Figure {}.{} requires non-empty caption_zh items".format(section_index, figure_index))
    if not data["evidence"]:
        raise ValueError("Evidence list must not be empty")
    for index, item in enumerate(data["evidence"], 1):
        if not item.get("claim") or not item.get("pages"):
            raise ValueError("Evidence item {} requires claim and pages".format(index))
        if any(not isinstance(page, int) or page < 1 for page in item["pages"]):
            raise ValueError("Evidence item {} has an invalid page".format(index))
    ids = [figure_id(f['source']) for s in article['sections'] for f in s.get('figures', [])]
    if len(ids) != len(set(ids)):
        raise ValueError('Each main figure must occur exactly once')
    return short_name


def build_markdown(data, figures=None):
    figures = figures or {}
    metadata = data["metadata"]
    article = data["article"]
    lines = [
        "# {}｜{}".format(metadata["journal_abbr"], article["headline"]),
        "",
        "本文分享一篇{}文章——《{}》，[引文链接]({})。".format(
            metadata["journal_abbr"], metadata["title"], reference_url(metadata)
        ),
        "",
    ]
    if is_preprint(metadata): lines.extend([preprint_notice(metadata), ''])
    if article.get("deck"):
        lines.extend(["*{}*".format(article["deck"]), ""])
    for paragraph in article.get("lead", []):
        lines.extend([paragraph, ""])

    for section in article["sections"]:
        lines.extend([("### " if section.get("level") == 3 else "## ") + section["heading"], ""])
        for paragraph in section.get("paragraphs", []):
            lines.extend([paragraph, ""])
        for bullet in section.get("bullets", []):
            lines.append("- {}".format(bullet))
        if section.get("bullets"):
            lines.append("")
        for figure in section.get("figures", []):
            key = figure_id(figure['source'])
            asset = figures.get(key, {}).get('path') or figure.get('asset')
            if not asset:
                raise ValueError(key + ': extract the original image before rendering an illustrated article')
            if Path(asset).is_absolute() or '..' in Path(asset).parts or not asset.startswith('assets/'):
                raise ValueError('Figure asset must be relative to the article directory under assets/')
            if figure.get('preview_status') == 'source-pages':
                lines.extend(['> **原页预览**：自动裁切未确认完整，以下保留相关原页，需对照核查。', ''])
            lines.extend(['![' + key + '](' + asset + ')', '', public_caption(figure), ''])

    lines.extend(["## 结语", ""])
    for paragraph in article["conclusion"]:
        lines.extend([paragraph, ""])
    lines.extend(["## 原文信息", "", article["reference"], ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Render import-compatible WeChat Markdown.")
    parser.add_argument("content", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--figures", type=Path, help="Extraction manifest; default reports/<stem>/figures.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.content.read_text(encoding="utf-8"))
    short_name = validate_content(data)
    metadata = data["metadata"]
    stem = "{}_{}_{}".format(
        date_component(metadata),
        safe_component(metadata["journal_abbr"]),
        safe_component(short_name),
    )
    bundle = args.root.resolve() / "reports" / stem
    output = bundle / (stem + ".md")
    manifest_path = args.figures or bundle / 'figures.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'figures': {}}
    if manifest.get('unresolved'):
        raise ValueError('Resolve all figure extraction candidates before rendering')
    write_file(output, build_markdown(data, manifest['figures']), force=args.force)
    print(output)


if __name__ == "__main__":
    main()
