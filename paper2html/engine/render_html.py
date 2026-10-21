#!/usr/bin/env python3
"""Read4MD Markdown to inline-styled HTML, without rewriting article text."""
import argparse
import base64
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from markdown_it import MarkdownIt
from .figure_format import find_figures

FONT = "'Helvetica Neue',Helvetica,'Hiragino Sans GB','Microsoft YaHei',Arial,sans-serif"
FEATURED = {"论文一句话", "研究背景", "模型架构", "研究结果", "贡献与局限", "结语"}
P_STYLE = "margin:0 0 8px;padding:0;min-height:24px;text-align:justify;line-height:1.75;font-size:16px;"
STYLES = {"p": P_STYLE, "strong":"font-weight:700;", "em":"font-style:italic;",
          "ul":"margin:8px 0 14px;padding-left:26px;", "ol":"margin:8px 0 14px;padding-left:26px;",
          "li":"margin:4px 0;", "a":"color:#576b95;text-decoration:underline;overflow-wrap:anywhere;",
          "blockquote":"margin:16px 0;padding:8px 16px;border-left:3px solid #abdcff;background:#f5f9fc;",
          "table":"border-collapse:collapse;width:100%;font-size:14px;",
          "th":"border:1px solid #ddd;padding:6px;", "td":"border:1px solid #ddd;padding:6px;"}


def parser():
    return MarkdownIt("commonmark", {"html":False, "breaks":True, "typographer":False}).enable(["table", "strikethrough"])


def plain(tokens):
    return ''.join(t.content if t.type in ("text", "code_inline") else plain(t.children) if t.children else ' ' for t in tokens)


def image_data(path):
    data = path.read_bytes()
    if len(data) > 20 * 1024 * 1024: raise ValueError("Image exceeds 20 MB: " + path.name)
    if data.startswith(b'\x89PNG\r\n\x1a\n'): mime = 'image/png'
    elif data.startswith(b'\xff\xd8\xff'): mime = 'image/jpeg'
    elif data[:6] in (b'GIF87a', b'GIF89a'): mime = 'image/gif'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP': mime = 'image/webp'
    else: raise ValueError("Only PNG/JPEG/GIF/WebP images are supported: " + path.name)
    return 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')


def local_path(value, root):
    path = (root / unquote(value)).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Image must exist inside the Markdown/manifest directory: " + value)
    return path


def load_figures(path):
    if path is None: return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('figures'), dict):
        raise ValueError('Manifest requires a figures object')
    result = {}
    for key, item in data['figures'].items():
        if not re.fullmatch(r'Figure [1-9]\d*', key): raise ValueError('Use Figure N manifest keys')
        if type(item.get('pdf_page')) is not int or item['pdf_page'] < 1: raise ValueError('Invalid pdf_page')
        asset = local_path(item['path'], path.parent)
        if item.get('sha256') and hashlib.sha256(asset.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Figure asset hash differs: ' + key)
        result[key] = {**item, 'uri':image_data(asset)}
    return result


def build(markdown, source, figures=None, fragment=False):
    md, figures = parser(), figures or {}
    tokens = md.parse(markdown)
    titles = [plain(tokens[i+1].children or []) for i,t in enumerate(tokens) if t.type=='heading_open' and t.tag=='h1']
    if len(titles) != 1: raise ValueError('Markdown must contain exactly one H1 title')
    warnings, used, image_count = [], set(), 0

    def image(uri, alt):
        nonlocal image_count
        image_count += 1
        return '<img src="' + html.escape(uri, quote=True) + '" alt="' + html.escape(alt, quote=True) + '" style="display:block;max-width:100%;height:auto;margin:16px auto 10px;">'

    def image_rule(renderer, items, index, options, env):
        t = items[index]; src = t.attrGet('src') or ''; url = urlsplit(src)
        if url.scheme.lower() in ('https','http'):
            warnings.append('远程图片依赖网络，公众号发布前需上传：' + src)
            uri = src
        elif not url.scheme and not url.netloc:
            uri = image_data(local_path(url.path, Path(source).parent))
        else: raise ValueError('Unsupported image URL')
        return image(uri, plain(t.children or []))

    def code_rule(renderer, items, index, options, env):
        return '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f5;padding:12px;font-size:13px;"><code>' + html.escape(items[index].content) + '</code></pre>\n'

    md.add_render_rule('image', image_rule)
    md.add_render_rule('fence', code_rule)
    md.add_render_rule('code_block', code_rule)
    modern = {f['start']: f for f in find_figures(markdown) if not f['legacy']}
    output, quote_stack, figure_depth = [], [], 0
    caption_open = False
    omit_heading = False
    for i, t in enumerate(tokens):
        if t.type == 'paragraph_open' and t.map and t.map[0] in modern:
            item = modern[t.map[0]]
            key = item['id']
            output.append(f'<section data-read4md-figure="{key}" style="margin:8px 0 20px;">')
            if key in figures:
                if key in used: raise ValueError('Duplicate figure: '+key)
                if item.get('image') and figures[key]['path'] != item['image']:
                    raise ValueError('Markdown image differs from manifest: '+key)
                used.add(key)
                if not item.get('image'): output.append(image(figures[key]['uri'],key))
            elif not item.get('image'):
                warnings.append(key+'：未提供图片')
            caption_open = True
            figure_depth += 1
        if t.type == 'heading_open':
            label = plain(tokens[i+1].children or [])
            omit_heading = fragment and t.tag == 'h1'
            if omit_heading: continue
            if t.tag == 'h2' and label in FEATURED:
                output.append('<h2 style="margin:20px 0 12px;text-align:center;font-size:16px;line-height:2;font-weight:700;"><span style="display:inline-block;background-color:rgba(145,211,246,0.14);border-left:4px solid #0396ff;padding:0 22px;max-width:100%;box-sizing:border-box;">')
                tokens[i+2].meta['featured'] = True
                continue
            size = 26 if t.tag == 'h1' else 16
            color = '' if t.tag == 'h1' else 'color:#ffa900;'
            t.attrSet('style', f'font-size:{size}px;line-height:1.75;margin:21px 0 8px;font-weight:700;{color}text-align:left;overflow-wrap:break-word;')
        elif t.type == 'heading_close':
            if omit_heading: omit_heading=False; continue
            if t.meta.get('featured'): output.append('</span></h2>\n'); continue
        elif t.type == 'inline':
            if omit_heading: continue
            for child in t.children or []:
                if child.tag in STYLES and child.nesting == 1: child.attrSet('style', STYLES[child.tag])
                if child.type == 'code_inline': child.attrSet('style','font-size:14px;background:#f4f4f4;padding:1px 4px;')
                if child.type == 'link_open':
                    href=child.attrGet('href') or ''
                    if urlsplit(href).scheme.lower() not in ('https','http','mailto',''):
                        raise ValueError('Unsupported link scheme')
            output.append(md.renderer.renderInline(t.children or [], md.options, {})); continue
        elif t.type == 'blockquote_open':
            depth, content = 1, []
            for following in tokens[i+1:]:
                if following.type == 'blockquote_open': depth += 1
                if following.type == 'blockquote_close': depth -= 1
                if depth == 0: break
                if following.type == 'inline': content.append(following.content)
            text = '\n'.join(content)
            is_figure = '**配图位置**' in text and '**中文图注**' in text
            quote_stack.append(is_figure)
            if is_figure:
                figure_depth += 1
                match=re.search(r'Figure\s+(\d+)[^\n]*?PDF\s*第\s*(\d+)\s*页',text)
                key='Figure '+match[1] if match else 'unresolved'
                output.append(f'<section data-read4md-figure="{key}" style="margin:18px 0 20px;">')
                if key in figures:
                    item=figures[key]
                    if item['pdf_page'] != int(match[2]): raise ValueError(key+': PDF page mismatch')
                    if key in used: raise ValueError('Duplicate figure: '+key)
                    used.add(key); output.append(image(item['uri'],key+'，PDF 第 '+match[2]+' 页'))
                else: warnings.append(key+'：未提供已核实图片，保留配图位置与完整图注')
                continue
        elif t.type == 'blockquote_close':
            if quote_stack.pop(): figure_depth -= 1; output.append('</section>\n'); continue
        if t.nesting == 1 and t.tag in STYLES:
            style=STYLES[t.tag]
            if t.tag=='p' and figure_depth: style=style.replace('font-size:16px','font-size:14px')
            t.attrSet('style',style)
        # Keep paragraphs inside lists visible so their inline styles survive editor import.
        t.hidden = False
        output.append(md.renderer.render([t],md.options,{}))
        if t.type == 'paragraph_close' and caption_open:
            output.append('</section>\n')
            caption_open = False
            figure_depth -= 1
    if set(figures)-used: raise ValueError('Manifest contains unused figures: '+', '.join(sorted(set(figures)-used)))
    digest=hashlib.sha256(markdown.encode()).hexdigest()
    body=(f'<section id="read4md-article" data-source-sha256="{digest}" data-omit-title="{str(fragment).lower()}" '
          f'style="max-width:677px;margin:0 auto;padding:20px 16px;box-sizing:border-box;background:#fff;font-family:{FONT};font-size:16px;color:#3e3e3e;line-height:1.75;overflow-wrap:break-word;">'+''.join(output)+'</section>')
    if fragment: document=body+'\n'
    else:
        document=('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                  '<meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: https: http:; style-src \'unsafe-inline\'; base-uri \'none\'">'
                  '<title>'+html.escape(titles[0])+'</title></head><body style="margin:0;background:#fff;">'+body+'</body></html>\n')
    return document, {'theme':'wechat-unicure','source_sha256':digest,'images':image_count,'mapped_figures':sorted(used),'warnings':warnings,'fragment':fragment}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('markdown',type=Path)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--figures',type=Path,help='Verified local image manifest')
    ap.add_argument('--fragment',action='store_true',help='Editor body fragment, omitting H1')
    ap.add_argument('--force',action='store_true',help='Explicitly replace existing HTML')
    args=ap.parse_args()
    article_dir = args.markdown.parent if args.markdown.parent.name == args.markdown.stem else args.markdown.parent / args.markdown.stem
    output = args.output or article_dir / (args.markdown.stem + '.html')
    if output.suffix.lower()!='.html' or output.resolve()==args.markdown.resolve(): ap.error('Output must be a separate .html file')
    source=args.markdown.read_text(encoding='utf-8')
    document,report=build(source,args.markdown,load_figures(args.figures),args.fragment)
    from .validate_html import validate_html
    report['validation']=validate_html(document,source,args.fragment)
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists() and output.read_text(encoding='utf-8')==document: report['unchanged']=True
    else:
        with output.open('w' if args.force else 'x',encoding='utf-8') as f:f.write(document)
    print(json.dumps({'output':str(output),**report},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
