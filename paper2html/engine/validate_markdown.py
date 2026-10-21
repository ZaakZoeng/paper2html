#!/usr/bin/env python3
import argparse
import json
import hashlib
import re
from pathlib import Path
from .figure_format import find_figures, figure_id


def validate(path, legacy=False):
    text = path.read_text(encoding="utf-8").strip()
    if len(text) < 1000:
        raise ValueError("Markdown appears too short: {} characters".format(len(text)))
    if len(re.findall(r"^# [^#]", text, re.M)) != 1:
        raise ValueError("Markdown must contain exactly one level-one title")
    title = re.match(r"\A# (?P<journal>[^|｜\n]+)｜(?P<headline>[^\n]+)\n\n", text)
    if not title:
        raise ValueError("Title must use: # 期刊缩写｜标题内容")
    opening = re.match(
        r"本文分享一篇(?P<journal>.+?)文章——《(?P<paper_title>.+)》，"
        r"\[引文链接\]\(https://doi\.org/10\.\d{4,9}/[^\s)]+\)。(?:\n\n|\Z)",
        text[title.end():],
    )
    if not opening:
        raise ValueError("Opening must use the fixed 本文分享 sentence and DOI link")
    if opening.group("journal") != title.group("journal"):
        raise ValueError("Journal abbreviation differs between title and opening")
    headings = re.findall(r"^## (.+)$", text, re.M)
    for required in ("论文一句话", "研究背景", "结语", "原文信息"):
        if required not in headings:
            raise ValueError("Markdown is missing required section: {}".format(required))
    if not re.search(r"https://doi\.org/10\.\d{4,9}/", text, re.I):
        raise ValueError("Markdown is missing a DOI link")
    if any(entity in text for entity in ("&amp;", "&lt;", "&gt;")):
        raise ValueError("Markdown contains unexpanded HTML entities")
    if legacy:
        figures = len(re.findall(r"^> \*\*配图位置\*\*：", text, re.M))
        captions = re.findall(r"^> \*\*中文图注\*\*：(.+)$", text, re.M)
        if len(captions) != figures:
            raise ValueError('Every legacy figure requires a caption')
    else:
        if '**配图位置**' in text or '**中文图注**' in text:
            raise ValueError('Legacy editorial figure labels must be converted to clean captions')
        entries = find_figures(text)
        figures = len(entries)
        captions = [f['caption_zh'][0] for f in entries]
        manifest_path = path.parent / 'figures.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else None
        if manifest and (manifest.get('unresolved') or set(manifest['figures']) != {f['id'] for f in entries}):
            raise ValueError('Figure extraction manifest and article do not agree')
        for item in entries:
            value = item.get('image')
            if not value or not value.startswith('assets/') or Path(value).is_absolute() or '..' in Path(value).parts:
                raise ValueError(item['id'] + ' must have an adjacent local image under assets/')
            image = (path.parent / value).resolve()
            if not image.is_relative_to(path.parent.resolve()) or not image.is_file():
                raise ValueError('Missing or invalid figure asset: ' + value)
            if manifest:
                record = manifest['figures'][item['id']]
                if record['path'] != value or hashlib.sha256(image.read_bytes()).hexdigest() != record['sha256']:
                    raise ValueError('Figure asset differs from provenance record')
        # A clean title without its next-line caption must never silently disappear.
        visible_ids = re.findall(r'^Figure ([1-9]\d*)，', text, re.M)
        if len(visible_ids) != figures:
            raise ValueError('A figure title has no complete next-line caption')
    for caption in captions:
        if not re.search(r"[\u4e00-\u9fff]", caption):
            raise ValueError('Figure caption must contain Chinese text')
        if re.search(r'(?:X{3,}|TODO|待补)', caption, re.I):
            raise ValueError('Figure caption contains placeholder text')

    return {
        "characters": len(text),
        "sections": headings,
        "figures": figures,
        "chinese_figure_captions": len(captions),
    }


def main():
    parser = argparse.ArgumentParser(description="Validate a generated WeChat Markdown file.")
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--legacy", action="store_true", help="Inspect old articles without requiring illustrated clean captions")
    args = parser.parse_args()
    if not args.markdown.is_file():
        raise FileNotFoundError(args.markdown)
    print("{}: {}".format(args.markdown, validate(args.markdown, legacy=args.legacy)))


if __name__ == "__main__":
    main()
