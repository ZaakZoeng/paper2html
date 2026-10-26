#!/usr/bin/env python3
import argparse
import re
from pathlib import Path
from .publication import ARXIV_ID


def validate(path):
    return validate_text(path.read_text(encoding="utf-8"))


def validate_text(text):
    text = text.strip()
    if len(text) < 1000:
        raise ValueError("Markdown appears too short: {} characters".format(len(text)))
    if len(re.findall(r"^# [^#]", text, re.M)) != 1:
        raise ValueError("Markdown must contain exactly one level-one title")
    title = re.match(r"\A# (?P<journal>[^|｜\n]+)｜(?P<headline>[^\n]+)\n\n", text)
    if not title:
        raise ValueError("Title must use: # 期刊缩写｜标题内容")
    opening = re.match(
        r"本文分享一篇(?P<journal>.+?)文章——《(?P<paper_title>.+)》，"
        r"\[引文链接\]\((?:https://doi\.org/10\.\d{4,9}/[^\s)]+|https://arxiv\.org/abs/"+ARXIV_ID+r")\)。(?:\n\n|\Z)",
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
    if not re.search(r"https://doi\.org/10\.\d{4,9}/|https://arxiv\.org/abs/"+ARXIV_ID, text, re.I):
        raise ValueError("Markdown is missing a verified source link")
    if 'https://arxiv.org/abs/' in opening.group(0) and '**预印本**' not in text:
        raise ValueError('arXiv source requires a visible preprint notice')
    if any(entity in text for entity in ("&amp;", "&lt;", "&gt;")):
        raise ValueError("Markdown contains unexpanded HTML entities")
    figures = len(re.findall(r"^> \*\*配图位置\*\*：", text, re.M))
    captions = re.findall(r"^> \*\*中文图注\*\*：(.+)$", text, re.M)
    if len(captions) != figures:
        raise ValueError("Every figure must have exactly one Chinese caption line")
    for caption in captions:
        if not re.search(r"[\u4e00-\u9fff]", caption):
            raise ValueError("Figure caption must contain Chinese text")
        if re.search(r"(?:X{3,}|TODO|待补)", caption, re.I):
            raise ValueError("Figure caption contains placeholder text")
    return {
        "characters": len(text),
        "sections": headings,
        "figure_placeholders": figures,
        "chinese_figure_captions": len(captions),
    }


def main():
    parser = argparse.ArgumentParser(description="Validate a generated WeChat Markdown file.")
    parser.add_argument("markdown", type=Path)
    args = parser.parse_args()
    if not args.markdown.is_file():
        raise FileNotFoundError(args.markdown)
    print("{}: {}".format(args.markdown, validate(args.markdown)))


if __name__ == "__main__":
    main()
