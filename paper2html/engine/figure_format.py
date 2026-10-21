"""Shared figure identification, public captions and safe writes."""
import re
from pathlib import Path
from markdown_it import MarkdownIt


def figure_id(source):
    match = re.fullmatch(r'(?:Figure|Fig\.?)\s*([1-9]\d*)(?:[A-Za-z](?:[-–—][A-Za-z])?)?', str(source).strip(), re.I)
    if not match:
        raise ValueError('Invalid main figure label: ' + str(source))
    return 'Figure ' + match[1]


def caption_text(parts):
    if isinstance(parts, str):
        return parts.strip()
    return '；'.join(p.strip().rstrip('。；') for p in parts) + '。'


def public_caption(figure):
    title = figure['title'].strip().rstrip('。')
    return figure_id(figure['source']) + '，' + title + '。  \n' + caption_text(figure['caption_zh'])


def find_figures(markdown):
    """Read real paragraph tokens so figure-like text in fenced code is ignored."""
    md = MarkdownIt('commonmark', {'html': False})
    tokens = md.parse(markdown)
    found = []
    for i, token in enumerate(tokens):
        if token.type != 'inline' or token.map is None:
            continue
        text = token.content
        legacy = re.fullmatch(r'\*\*配图位置\*\*：(.+?)，PDF\s*第\s*(\d+)\s*页，(.+?)。?\n\*\*中文图注\*\*：(.+)', text, re.S)
        modern = re.fullmatch(r'(Figure [1-9]\d*)，([^\n]+?)。\s*\n(.+)', text, re.S)
        if not legacy and not modern:
            continue
        if legacy:
            source, page, title, caption = legacy.groups()
            item = {'source': source, 'pdf_page': int(page), 'title': title.rstrip('。'), 'caption_zh': [caption.strip()], 'legacy': True}
        else:
            source, title, caption = modern.groups()
            item = {'source': source, 'title': title, 'caption_zh': [caption.strip()], 'legacy': False}
        item['id'] = figure_id(item['source'])
        item['start'], item['end'] = token.map
        # A single image paragraph immediately before the caption is its image.
        if i >= 3 and tokens[i-3].type == 'inline':
            previous = tokens[i-3]
            children = previous.children or []
            if len(children) == 1 and children[0].type == 'image':
                item['image'] = children[0].attrGet('src')
                item['image_start'], item['image_end'] = previous.map
        found.append(item)
    ids = [f['id'] for f in found]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate main figure captions')
    return found


def write_file(path, data, force=False):
    path = Path(path)
    if isinstance(data, str):
        data = data.encode('utf-8')
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb' if force else 'xb') as handle:
        handle.write(data)
